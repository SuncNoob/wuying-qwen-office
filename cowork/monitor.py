"""Local laptop dashboard for three 无影 Agentic Computers."""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from cowork.live import bus_root, collect_snapshot, dispatch_tasks
from cowork.localcfg import config_path, load_config


def dashboard_html() -> bytes:
    path = Path(__file__).with_name("static") / "monitor.html"
    return path.read_bytes()


def make_handler(
    snapshot_fn: Callable[[], dict[str, Any]],
    dispatch_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                try:
                    self._send(200, dashboard_html(), "text/html; charset=utf-8")
                except FileNotFoundError:
                    self._send(500, b"monitor.html missing", "text/plain")
                return
            if path == "/api/snapshot":
                try:
                    payload = snapshot_fn()
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                except Exception as exc:
                    body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self._send(500, body, "application/json; charset=utf-8")
                return
            self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/dispatch":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, b'{"ok":false,"error":"invalid json"}', "application/json")
                return
            try:
                result = dispatch_fn(body if isinstance(body, dict) else {})
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self._send(200 if result.get("ok") else 409, payload, "application/json; charset=utf-8")
            except Exception as exc:
                payload = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(500, payload, "application/json; charset=utf-8")

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    root: str | Path | None = None,
) -> None:
    explicit_root = Path(root).expanduser().resolve() if root else None

    def current_cfg() -> dict[str, Any]:
        return load_config()

    def snapshot() -> dict[str, Any]:
        cfg = current_cfg()
        return collect_snapshot(cfg, bus_root(cfg, explicit_root))

    def dispatch(body: dict[str, Any]) -> dict[str, Any]:
        cfg = current_cfg()
        return dispatch_tasks(body, cfg=cfg)

    handler = make_handler(snapshot, dispatch)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    boot_cfg = current_cfg()
    bus = bus_root(boot_cfg, explicit_root)
    print(f"cowork monitor {url}  root={bus}  config={config_path()} (reload on each snapshot)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nmonitor stopped")
    finally:
        httpd.server_close()
