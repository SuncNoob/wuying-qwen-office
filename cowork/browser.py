"""Headless Chrome fetch and product-image extraction. Stdlib + system Chrome."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from cowork.protocol import host_allowed

USER_AGENT = "Cowork/0.1 (+https://github.com/wuying-multiac-cowork)"
BROWSER_LEASE_SECONDS = 2400
DEFAULT_MAX_IMAGES = 16
MIN_IMAGE_BYTES = 8_192
MAX_IMAGE_BYTES = 3_000_000

BRAND_HOSTS = {
    "lienujewelry.com": "lienu",
    "hasuna.com": "hasuna",
    "lesbonbon.jp": "les-bon-bon",
    "artidaoud.com": "artida-oud",
    "synchronicity-silver.com": "synchronicity",
    "sirisiri.jp": "siri-siri",
    "lucine.jp": "lucine",
    "mariha.jp": "mariha",
}

SKIP_URL_BITS = (
    "favicon",
    "sprite",
    "icon",
    "logo",
    "pixel",
    "1x1",
    "blank.",
    "spacer",
    "loading.svg",
    "placeholder",
)


class _ImageParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base = base
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta" and ad.get("property") in {"og:image", "og:image:url"}:
            self._add(ad.get("content", ""))
        if tag.lower() == "link" and "image" in ad.get("rel", ""):
            self._add(ad.get("href", ""))
        if tag.lower() != "img":
            return
        for key in ("src", "data-src", "data-original", "data-lazy", "data-zoom"):
            self._add(ad.get(key, ""))
        self._add_srcset(ad.get("srcset", "") or ad.get("data-srcset", ""))

    def _add_srcset(self, srcset: str) -> None:
        if not srcset:
            return
        parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
        if parts:
            self._add(parts[-1])

    def _add(self, raw: str) -> None:
        raw = raw.strip()
        if not raw or raw.startswith("data:"):
            return
        self.urls.append(urljoin(self.base, raw))


def brand_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for suffix, brand in BRAND_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return brand
    return (host.split(".")[0] if host else "misc").replace("www", "misc")


def looks_like_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(bit in url.lower() for bit in SKIP_URL_BITS):
        return False
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return True
    if "cdn.shopify.com" in url or "/files/" in path or "/products/" in path:
        return True
    return "image" in path or "img" in path


def parse_images(html: str, base_url: str) -> list[str]:
    parser = _ImageParser(base_url)
    try:
        parser.feed(html)
    except Exception:
        pass
    found = list(parser.urls)
    found.extend(_jsonld_images(html, base_url))
    found.extend(re.findall(r"https?://[^\"'\s>]+\.(?:jpg|jpeg|png|webp)", html, re.I))
    ordered: list[str] = []
    seen: set[str] = set()
    for url in found:
        url = url.split("?")[0] if "svg" in url.lower() else url
        if url in seen or not looks_like_image_url(url):
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _jsonld_images(html: str, base: str) -> list[str]:
    out: list[str] = []
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        out.extend(_walk_images(data, base))
    return out


def _walk_images(node: object, base: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, val in node.items():
            if key in {"image", "thumbnail", "photo"} and isinstance(val, str):
                found.append(urljoin(base, val))
            elif key in {"image", "thumbnail"} and isinstance(val, list):
                found.extend(_walk_images(val, base))
            else:
                found.extend(_walk_images(val, base))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_images(item, base))
    return found


def chrome_bin() -> str | None:
    for name in ("google-chrome", "chromium-browser", "chromium", "wuying-browser-use-chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def dump_dom(url: str, timeout: int = 35) -> tuple[str, str]:
    """Return (html, engine). engine is chrome or http."""
    import os
    import signal

    chrome = chrome_bin()
    if chrome:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--virtual-time-budget=8000",
            "--dump-dom",
            url,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                stdout, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                stdout = b""
            html = (stdout or b"").decode("utf-8", "replace")
            if len(html) > 500:
                return html, "chrome"
        except OSError:
            pass
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), "http"


def shopify_images(page_url: str, allow_hosts: list[str], limit: int = 24) -> list[str]:
    parsed = urlparse(page_url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}/products.json?limit={limit}"
    if not host_allowed(endpoint, allow_hosts):
        return []
    try:
        req = Request(endpoint, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []
    urls: list[str] = []
    for product in data.get("products") or []:
        for image in product.get("images") or []:
            src = image.get("src") if isinstance(image, dict) else None
            if src:
                urls.append(src)
        image = product.get("image")
        if isinstance(image, dict) and image.get("src"):
            urls.append(image["src"])
    return urls


def download_image(url: str, dest_dir, allow_hosts: list[str], index: int) -> dict | None:
    if not host_allowed(url, allow_hosts):
        return {"url": url, "ok": False, "error": "host not allowed"}
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            body = resp.read(MAX_IMAGE_BYTES + 1)
            ctype = str(resp.headers.get("Content-Type", ""))
        if len(body) < MIN_IMAGE_BYTES:
            return None
        if len(body) > MAX_IMAGE_BYTES:
            return None
        if "svg" in ctype or "html" in ctype:
            return None
        ext = _ext(url, ctype)
        name = f"{index:02d}{_slug(urlparse(url).path)}{ext}"
        path = dest_dir / name
        path.write_bytes(body)
        return {
            "url": url,
            "ok": True,
            "file": str(path),
            "bytes": len(body),
            "content_type": ctype,
        }
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc)}


def _ext(url: str, ctype: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    if "png" in ctype:
        return ".png"
    if "webp" in ctype:
        return ".webp"
    return ".jpg"


def _slug(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    base = re.sub(r"\.[a-z0-9]+$", "", base, flags=re.I)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:40]
    return ("-" + cleaned) if cleaned else ""
