"""Run Codex on the Agentic Computer so Wuying gateway sessions are visible."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

CODEX_TIMEOUT_SECONDS = 1800
CODEX_LEASE_SECONDS = 2400
CODEX_MODES = {"browser", "research", "codex"}
DEFAULT_MAX_IMAGES = 16
MIN_PDP_PAGES = 12


def uses_codex_session(mode: str) -> bool:
    return (mode or "") in CODEX_MODES


def jewelry_prompt(brand: str, url: str, allow_hosts: list[str], limit: int, pic_dir: str) -> str:
    hosts = ", ".join(allow_hosts)
    need = min(limit, MIN_PDP_PAGES)
    return f"""You are a 无影 Agentic Computer. Use THIS machine's Codex tools and the browser-use MCP (real Chrome) so the session is visible in the Wuying console. Consume this Agentic Computer's own Wuying gateway token. Do not ask for approval.

Brand: {brand}
Official listing URL (start here, this is NOT the image source): {url}
Image output directory (create if needed, overwrite old files): {pic_dir}
Save {limit} unique product photos from at least {need} distinct product detail pages.
Allowed hosts only: {hosts}

Hard rules — listing-page thumbnails are a FAIL:
1. Open the official listing in the browser. Do not use a search engine.
2. Click into individual product DETAIL pages (子页面). Required URL shapes:
   - Shopify: /products/<slug>
   - ARTIDA OUD: /item/detail/...
   - synchronicity / BASE: /items/<id>
   Do NOT download images while still on /collections/, category, or home.
3. On each detail page, save the MAIN product photo of the jewelry itself (hero / og:image / first gallery still). Prefer the largest file (width>=800 if possible). Use curl or the browser download.
4. One product page → one file. No duplicate SKUs, no two sizes of the same CDN asset.
5. Skip logos, favicons, banners, models-only lifestyle if no jewelry is visible, payment icons, about-us stones, campaign KV.
6. Filenames: 01.jpg … {limit:02d}.jpg (png/webp ok).
7. Write {pic_dir}/manifest.json as a JSON array:
   [{{"file":"01.jpg","source_page":"https://.../products/...","image_url":"https://cdn...","product":"name"}}]
   source_page MUST be the detail URL from step 2, never the listing URL.

When finished, print DONE, how many distinct detail pages you opened, and the file list.
If you cannot open detail pages, print FAIL and why. Saving {limit} images all from the listing page is FAIL.
"""


def research_prompt(title: str, body: str, path: str) -> str:
    problem = (body or title or "stated mathematical problem").strip()
    return f"""You are a 无影 Agentic Computer. Use THIS machine's Codex so the session is visible in the Wuying console. Consume this Agentic Computer's own Wuying gateway token. Do not ask for approval.

This is a collaborative research attempt, not a claim that an open problem is solved.

Title: {title}
Problem: {problem}
Write the research note to: {path}

The Markdown file MUST contain these headings in Chinese:
## 命题
## 已知结果
## 尝试
## 缺口

Hard rules:
1. Do not write 已完全证明 or Q.E.D. for an open conjecture unless you also exhibit a counterexample.
2. In 缺口, state clearly what remains unproved.
3. If a small finite check helps, also write a sibling .py next to the markdown that runs with python3 and prints OK.
4. Create parent directories if needed. Overwrite the note if it already exists.

When finished, print DONE and the file list. If you cannot write the note, print FAIL and why.
"""


def coding_prompt(title: str, body: str, path: str, need: list[str]) -> str:
    files = ", ".join(need) if need else path or "the project files listed in the brief"
    brief = (body or title or "build the requested application").strip()
    return f"""You are a 无影 Agentic Computer. Use THIS machine's Codex so the session is visible in the Wuying console. Consume this Agentic Computer's own Wuying gateway token. Do not ask for approval.

This is collaborative AI coding through a Git bus. Write a real, runnable application in this repository.

Title: {title}
Brief:
{brief}

Primary path (must exist when you finish): {path}
Also create these files: {files}

Hard rules:
1. Follow the Brief. Do not invent a different product. 千问办公 is an AI coding agent (like Qoder / Claude Code / Codex), not a calendar or todo secretary.
2. Use the AgentScope Python framework (https://github.com/agentscope-ai/agentscope). Prefer AgentScope 2.x (`from agentscope.agent import Agent`, DashScopeChatModel, Toolkit with file/shell tools such as Bash, Grep, Glob, Read, Write, Edit). If python3 is older than 3.11, use AgentScope 1.x (`ReActAgent`) instead — still import agentscope.
3. Model is Qwen via DashScope. Read DASHSCOPE_API_KEY from the environment. Do not hardcode secrets.
4. Include README.md (product + how to run), requirements.txt, .env.example with DASHSCOPE_API_KEY=, and a single entrypoint (app.py or equivalent).
5. No network calls required at import time. `python3 -c "import app"` or the documented CLI `--help` should work without a real API key. Provide `--demo` that shows an agent coding loop without calling DashScope.
6. Do not commit .env. Overwrite files if they already exist. Keep the .cowork/ protocol tree intact.

When finished, print DONE and the file list. If you cannot build it, print FAIL and why.
"""


def codex_bin() -> str | None:
    return shutil.which("codex")


def run_codex(prompt: str, cwd: Path, timeout: int = CODEX_TIMEOUT_SECONDS) -> dict:
    binary = codex_bin()
    if not binary:
        return {"ok": False, "code": 127, "stdout": "", "stderr": "codex not found", "cmd": []}
    cwd = cwd.resolve()
    cmd = [
        binary,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-s",
        "danger-full-access",
        "-C",
        str(cwd),
        "-c",
        f'projects."{cwd}".trust_level="trusted"',
        prompt,
    ]
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": stdout[-8000:],
            "stderr": stderr[-4000:],
            "cmd": cmd[:8],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "code": 124,
            "stdout": (exc.stdout or b"").decode("utf-8", "replace")[-4000:],
            "stderr": "codex exec timed out",
            "cmd": cmd[:8],
        }
    except OSError as exc:
        return {"ok": False, "code": 1, "stdout": "", "stderr": str(exc), "cmd": cmd[:8]}
