"""Scenario executors. Stdlib only so they run on 无影 AC without pip."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from cowork.browser import (
    DEFAULT_MAX_IMAGES,
    brand_from_url,
)
from cowork.codex_run import MIN_PDP_PAGES, coding_prompt, jewelry_prompt, research_prompt, run_codex
from cowork.protocol import Task, dump_json, host_allowed, now
from cowork.store import Store

USER_AGENT = "Cowork/0.1 (+https://github.com/wuying-multiac-cowork)"
RESEARCH_HEADINGS = ("## 命题", "## 已知结果", "## 尝试", "## 缺口")


def workspace_dest(store: Store, path: str) -> Path:
    if not path:
        raise ValueError("implement task missing path")
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"refusing path outside workspace: {path}")
    dest = (store.root / rel).resolve()
    if dest != store.root.resolve() and store.root.resolve() not in dest.parents:
        raise ValueError(f"refusing path outside workspace: {path}")
    return dest


def research_note_ok(text: str) -> bool:
    if len(text.strip()) < 200:
        return False
    return all(heading in text for heading in RESEARCH_HEADINGS)


def coding_app_ok(root: Path, path: str, need: list[str]) -> bool:
    required = [p for p in ([path] if path else []) + list(need or [])]
    if not required:
        required = [path] if path else []
    for rel in required:
        if not rel:
            continue
        if not (root / rel).exists():
            return False
    py_files = list(root.rglob("*.py"))
    py_files = [p for p in py_files if ".cowork" not in p.parts and "cowork" not in p.parts]
    return any("agentscope" in p.read_text(encoding="utf-8", errors="replace") for p in py_files)


def run_implement(store: Store, task: Task) -> dict:
    dest = workspace_dest(store, task.path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if task.mode == "research" or task.scenario == "research":
        return _implement_research(store, task, dest)
    if task.mode == "codex":
        return _implement_coding_codex(store, task, dest)
    dest.write_text(task.content if task.content else f"{task.title}\n{task.body}\n", encoding="utf-8")
    result = {
        "ok": True,
        "path": task.path,
        "bytes": dest.stat().st_size,
        "at": now(),
    }
    dump_json(store.result_dir(task.id) / "implement.json", result)
    return result


def _implement_coding_codex(store: Store, task: Task, dest: Path) -> dict:
    prompt = coding_prompt(task.title, task.body, task.path, list(task.need or []))
    last = run_codex(prompt, store.root)
    ok = dest.exists() and coding_app_ok(store.root, task.path, list(task.need or []))
    result = {
        "ok": ok,
        "path": task.path,
        "bytes": dest.stat().st_size if dest.exists() else 0,
        "engine": "codex",
        "codex_code": last.get("code"),
        "at": now(),
        "error": "" if ok else "coding deliverable missing required files or agentscope import",
    }
    dump_json(store.result_dir(task.id) / "implement.json", result)
    (store.result_dir(task.id) / "codex.log").write_text(
        (last.get("stdout") or "") + "\n--- stderr ---\n" + (last.get("stderr") or ""),
        encoding="utf-8",
    )
    return result


def _implement_research(store: Store, task: Task, dest: Path) -> dict:
    prompt = research_prompt(task.title, task.body, task.path)
    last = run_codex(prompt, store.root)
    text = dest.read_text(encoding="utf-8") if dest.exists() else ""
    ok = dest.exists() and research_note_ok(text)
    result = {
        "ok": ok,
        "path": task.path,
        "bytes": dest.stat().st_size if dest.exists() else 0,
        "engine": "codex",
        "codex_code": last.get("code"),
        "at": now(),
        "error": "" if ok else "research note missing required sections or too short",
    }
    dump_json(store.result_dir(task.id) / "implement.json", result)
    (store.result_dir(task.id) / "codex.log").write_text(
        (last.get("stdout") or "") + "\n--- stderr ---\n" + (last.get("stderr") or ""),
        encoding="utf-8",
    )
    return result


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def fetch_url(url: str, timeout: int = 20) -> tuple[int, str, bytes]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:  # nosec B310 - scheme checked by host_allowed
        body = resp.read()
        return int(getattr(resp, "status", 200) or 200), str(resp.headers.get("Content-Type", "")), body


def extract_title(html: bytes) -> str:
    parser = _TitleParser()
    try:
        parser.feed(html.decode("utf-8", "replace"))
    except Exception:
        return ""
    return parser.title.strip()


def run_review(store: Store, task: Task, tasks: dict[str, Task]) -> dict:
    notes: list[str] = []
    ok = True
    for dep in task.depends_on:
        parent = tasks.get(dep)
        if parent is None:
            ok = False
            notes.append(f"missing dependency {dep}")
            continue
        if parent.path:
            target = store.root / parent.path
            if not target.exists():
                ok = False
                notes.append(f"missing file {parent.path}")
            else:
                notes.append(f"found {parent.path} ({target.stat().st_size} bytes)")
                if parent.mode == "research" or parent.scenario == "research":
                    text = target.read_text(encoding="utf-8")
                    if not research_note_ok(text):
                        ok = False
                        notes.append("research note missing 命题/已知结果/尝试/缺口 or too short")
                    else:
                        notes.append("research sections present")
                if parent.mode == "codex":
                    if not coding_app_ok(store.root, parent.path, list(parent.need or [])):
                        ok = False
                        notes.append("coding app missing required files or agentscope import")
                    else:
                        notes.append("agentscope app files present")
        implement = store.result_dir(dep) / "implement.json"
        if implement.exists():
            notes.append(f"implement result present for {dep}")
        else:
            ok = False
            notes.append(f"no implement result for {dep}")
    result = {"ok": ok, "notes": notes, "at": now()}
    dump_json(store.result_dir(task.id) / "review.json", result)
    (store.result_dir(task.id) / "review.md").write_text(
        ("PASS" if ok else "FAIL") + "\n" + "\n".join(notes) + "\n",
        encoding="utf-8",
    )
    return result


def run_enqueue(store: Store, task: Task) -> dict:
    if not task.seed_urls:
        raise ValueError("enqueue task has no seed_urls")
    if not task.allow_hosts:
        raise ValueError("enqueue task requires allow_hosts")
    accepted: list[str] = []
    rejected: list[str] = []
    for url in task.seed_urls:
        if host_allowed(url, task.allow_hosts):
            accepted.append(url)
        else:
            rejected.append(url)
    payload = {
        "seed_task": task.id,
        "allow_hosts": task.allow_hosts,
        "urls": accepted,
        "rejected": rejected,
        "at": now(),
    }
    dump_json(store.cowork / "queue" / f"{task.id}.json", payload)
    dump_json(store.result_dir(task.id) / "enqueue.json", payload)
    return payload


def run_fetch(store: Store, task: Task) -> dict:
    queue = _queue_for(store, task)
    allow_hosts = task.allow_hosts or queue.get("allow_hosts") or []
    urls = task.seed_urls or queue.get("urls") or []
    if not urls:
        raise ValueError("fetch task has no urls")
    fetched = []
    for url in urls:
        if not host_allowed(url, allow_hosts):
            fetched.append({"url": url, "ok": False, "error": "host not allowed"})
            continue
        if task.mode == "browser":
            fetched.append(_fetch_browser_page(store, task, url, allow_hosts))
            continue
        try:
            status, content_type, body = fetch_url(url)
            raw_path = store.result_dir(task.id) / _safe_name(url)
            raw_path.write_bytes(body)
            fetched.append(
                {
                    "url": url,
                    "ok": True,
                    "status": status,
                    "content_type": content_type,
                    "bytes": len(body),
                    "file": str(raw_path.relative_to(store.root)),
                    "title": extract_title(body),
                    "engine": "http",
                }
            )
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            fetched.append({"url": url, "ok": False, "error": str(exc)})
    result = {"ok": all(item.get("ok") for item in fetched), "fetched": fetched, "at": now()}
    dump_json(store.result_dir(task.id) / "fetch.json", result)
    return result


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PDP_MARKERS = ("/products/", "/item/detail", "/items/")
LISTING_MARKERS = ("/collections/", "category_id=", "/category/")


def _clear_pic_dir(pic_dir: Path) -> None:
    if not pic_dir.exists():
        return
    for path in pic_dir.iterdir():
        if path.name == "manifest.json" or path.suffix.lower() in IMAGE_SUFFIXES:
            path.unlink()


def required_pdp_pages(limit: int) -> int:
    if limit <= 0:
        return MIN_PDP_PAGES
    if limit < 8:
        return max(1, limit)
    return min(limit, MIN_PDP_PAGES)


def is_pdp_url(url: str) -> bool:
    low = (url or "").lower()
    if not low:
        return False
    return any(m in low for m in PDP_MARKERS)


def pdp_page_count(manifest: list[dict]) -> int:
    pages = set()
    for item in manifest:
        page = str(item.get("source_page") or "").split("?")[0].rstrip("/")
        if is_pdp_url(page):
            pages.add(page)
    return len(pages)


def load_manifest(pic_dir: Path) -> list[dict]:
    path = pic_dir / "manifest.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _fetch_browser_page(store: Store, task: Task, url: str, allow_hosts: list[str]) -> dict:
    """Dispatch to Codex on this Agentic Computer (Wuying gateway session)."""
    brand = task.brand or brand_from_url(url)
    limit = task.max_images or DEFAULT_MAX_IMAGES
    pic_dir = store.root / "pics" / brand
    pic_dir.mkdir(parents=True, exist_ok=True)
    _clear_pic_dir(pic_dir)
    rel_dir = str(pic_dir.relative_to(store.root))
    need = required_pdp_pages(limit)
    last = {}
    ok_harvest = False
    for attempt in range(2):
        prompt = jewelry_prompt(brand, url, allow_hosts, limit, rel_dir)
        if attempt:
            prompt += (
                "\n\nRETRY: previous save was listing thumbnails or too few detail pages. "
                f"You MUST click at least {need} product detail URLs before downloading anything."
            )
        last = run_codex(prompt, store.root)
        images = _list_saved_images(store.root, pic_dir)
        pdps = pdp_page_count(load_manifest(pic_dir))
        ok_harvest = bool(images) and pdps >= need
        if ok_harvest:
            break
    images = _list_saved_images(store.root, pic_dir)
    pdps = pdp_page_count(load_manifest(pic_dir))
    dump_json(
        store.result_dir(task.id) / "images.json",
        {
            "brand": brand,
            "page": url,
            "engine": "codex",
            "codex_code": last.get("code"),
            "pdp_pages": pdps,
            "required_pdp_pages": need,
            "images": images,
        },
    )
    notes = store.result_dir(task.id) / "codex.log"
    notes.write_text(
        (last.get("stdout") or "") + "\n--- stderr ---\n" + (last.get("stderr") or ""),
        encoding="utf-8",
    )
    return {
        "url": url,
        "ok": ok_harvest,
        "engine": "codex",
        "title": brand,
        "brand": brand,
        "images": images,
        "pdp_pages": pdps,
        "codex_code": last.get("code"),
        "file": str(notes.relative_to(store.root)),
        "bytes": sum(item.get("bytes") or 0 for item in images),
        "error": "" if ok_harvest else f"need {need} product detail pages, got {pdps}",
    }


def _list_saved_images(root: Path, pic_dir: Path) -> list[dict]:
    images = []
    if not pic_dir.exists():
        return images
    for path in sorted(pic_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            continue
        images.append(
            {
                "ok": True,
                "file": str(path.resolve().relative_to(root.resolve())),
                "bytes": path.stat().st_size,
            }
        )
    return images


def run_extract(store: Store, task: Task, tasks: dict[str, Task]) -> dict:
    rows = []
    catalog_lines = ["# Product images", ""]
    for dep in task.depends_on:
        fetch_result = store.result_dir(dep) / "fetch.json"
        if not fetch_result.exists():
            continue
        data = json.loads(fetch_result.read_text(encoding="utf-8"))
        for item in data.get("fetched") or []:
            images = item.get("images") or []
            row = {
                "source_task": dep,
                "url": item.get("url"),
                "ok": item.get("ok"),
                "status": item.get("status"),
                "title": item.get("title"),
                "brand": item.get("brand"),
                "engine": item.get("engine"),
                "bytes": item.get("bytes"),
                "image_count": len(images),
                "images": [img.get("file") for img in images if img.get("ok")],
            }
            rows.append(row)
            brand = row.get("brand") or "misc"
            catalog_lines.append(f"## {brand}")
            catalog_lines.append(f"- page: {row.get('url')}")
            catalog_lines.append(f"- engine: {row.get('engine')}")
            for img in row["images"]:
                catalog_lines.append(f"- ![{brand}]({img})")
            catalog_lines.append("")
    jsonl = store.result_dir(task.id) / "structured.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    catalog = store.root / "pics" / "CATALOG.md"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("\n".join(catalog_lines).rstrip() + "\n", encoding="utf-8")
    has_images = any(r.get("image_count", 0) > 0 for r in rows)
    if has_images:
        ok = bool(rows) and all(r.get("ok") for r in rows)
    else:
        ok = bool(rows) and all(r.get("ok") for r in rows)
    result = {
        "ok": ok,
        "rows": len(rows),
        "file": str(jsonl.relative_to(store.root)),
        "catalog": str(catalog.relative_to(store.root)),
        "at": now(),
    }
    dump_json(store.result_dir(task.id) / "extract.json", result)
    return result


def _queue_for(store: Store, task: Task) -> dict:
    for dep in task.depends_on:
        q = store.cowork / "queue" / f"{dep}.json"
        if q.exists():
            return json.loads(q.read_text(encoding="utf-8"))
    files = sorted((store.cowork / "queue").glob("*.json"))
    if files:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    return {}


def _safe_name(url: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in url)
    return (cleaned[:80] or "page") + ".html"
