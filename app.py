#!/usr/bin/env python3
"""
千问办公 — Agentic Coding Agent
================================
An AI coding agent built with AgentScope 2.x and Qwen (DashScope).

Similar to Qoder / Claude Code / OpenAI Codex: the user describes a
development task in natural language, and the agent reads code, writes
code, runs commands, and delivers reviewable results in a local workspace.

NOT an office secretary, calendar, or todo-list app.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Lazy AgentScope loader — keeps --help / import fast and key-free
# ---------------------------------------------------------------------------

_agentscope_loaded = False


def _load_agentscope():
    """Import agentscope (deferred so --help never needs it)."""
    global _agentscope_loaded
    if _agentscope_loaded:
        return
    try:
        import agentscope  # noqa: F401
        _agentscope_loaded = True
    except ImportError:
        print(
            "Error: agentscope is not installed.\n"
            "Run:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Tool-Call Trajectory — shared by demo & real mode
# ---------------------------------------------------------------------------

class ToolCall:
    """One step in the agent's execution trace."""

    def __init__(self, tool: str, args: dict, result: str, duration_ms: int = 0):
        self.tool = tool
        self.args = args
        self.result = result
        self.duration_ms = duration_ms

    def __repr__(self):
        return f"ToolCall({self.tool}, {self.args})"


def _print_trajectory(calls: list, final_answer: str) -> None:
    """Pretty-print the agent's tool-call trajectory."""
    print()
    print("=" * 64)
    print("  🔧  Tool-Call Trajectory")
    print("=" * 64)
    for i, c in enumerate(calls, 1):
        print(f"\n  [{i}] {c.tool}  ({c.duration_ms} ms)")
        for k, v in c.args.items():
            val = str(v)
            if len(val) > 120:
                val = val[:117] + "..."
            print(f"      {k}: {val}")
        result_preview = c.result
        if len(result_preview) > 300:
            result_preview = result_preview[:297] + "..."
        print(f"      → {result_preview}")
    print()
    print("-" * 64)
    print("  💬  Final Answer")
    print("-" * 64)
    print(f"  {final_answer}")
    print("=" * 64)
    print()


# ---------------------------------------------------------------------------
# Demo Mode — full coding loop, zero API calls
# ---------------------------------------------------------------------------

def _run_demo(workspace: Path) -> None:
    """Simulate a complete coding loop inside *workspace*.

    1. Create a sample Python file
    2. "Read" it  (tool: read_file)
    3. "Edit" it  (tool: edit_file)
    4. "Run" a syntax check  (tool: run_shell)
    5. Print the full trajectory
    """
    workspace.mkdir(parents=True, exist_ok=True)
    sample = workspace / "hello.py"

    # Step 0 — seed a file the agent will work on
    sample.write_text(
        textwrap.dedent("""\
            def greet(name):
                return "Hello, " + name

            if __name__ == "__main__":
                print(greet("World"))
        """),
        encoding="utf-8",
    )
    print(f"📁 Workspace: {workspace}")
    print(f"📄 Created sample file: {sample}")

    trajectory = []

    # --- Step 1: read_file ------------------------------------------------
    t0 = time.monotonic()
    content = sample.read_text(encoding="utf-8")
    trajectory.append(ToolCall(
        tool="read_file",
        args={"path": str(sample)},
        result=content.strip(),
        duration_ms=int((time.monotonic() - t0) * 1000),
    ))

    # --- Step 2: edit_file — add type hints & docstring -------------------
    t0 = time.monotonic()
    new_content = textwrap.dedent('''\
        def greet(name: str) -> str:
            """Return a greeting for *name*."""
            return f"Hello, {name}!"

        if __name__ == "__main__":
            print(greet("World"))
    ''')
    sample.write_text(new_content, encoding="utf-8")
    trajectory.append(ToolCall(
        tool="edit_file",
        args={
            "path": str(sample),
            "old": 'def greet(name):\n    return "Hello, " + name',
            "new": 'def greet(name: str) -> str:\n    """Return a greeting for *name*."""\n    return f"Hello, {name}!"',
        },
        result="File updated successfully.",
        duration_ms=int((time.monotonic() - t0) * 1000),
    ))

    # --- Step 3: run_shell — syntax check ---------------------------------
    t0 = time.monotonic()
    import py_compile
    try:
        py_compile.compile(str(sample), doraise=True)
        shell_result = "Syntax OK"
    except py_compile.PyCompileError as exc:
        shell_result = str(exc)
    trajectory.append(ToolCall(
        tool="run_shell",
        args={"command": f"python3 -m py_compile {sample}"},
        result=shell_result,
        duration_ms=int((time.monotonic() - t0) * 1000),
    ))

    # --- Step 4: run_shell — execute the script ---------------------------
    t0 = time.monotonic()
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(sample)],
        capture_output=True, text=True, cwd=str(workspace),
    )
    trajectory.append(ToolCall(
        tool="run_shell",
        args={"command": f"python3 {sample}"},
        result=proc.stdout.strip() or proc.stderr.strip(),
        duration_ms=int((time.monotonic() - t0) * 1000),
    ))

    final = (
        'Done. I added type hints and a docstring to `greet()`, '
        'verified syntax, and ran the script — output: "Hello, World!"'
    )
    _print_trajectory(trajectory, final)


# ---------------------------------------------------------------------------
# Real Agent Mode — AgentScope 2.x + DashScope
# ---------------------------------------------------------------------------

def _build_agent(workspace: Path):
    """Create an AgentScope 2.x coding agent with tooling.

    Returns the agent object or raises on missing API key.
    """
    _load_agentscope()

    from agentscope.agent import Agent, ReActConfig
    from agentscope.credential import DashScopeCredential
    from agentscope.model import DashScopeChatModel
    from agentscope.tool import Toolkit, Read, Write, Edit, Bash, Glob, Grep

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print(
            "Error: DASHSCOPE_API_KEY is not set.\n"
            "Copy .env.example → .env and fill in your key, or use --demo.",
            file=sys.stderr,
        )
        sys.exit(1)

    model_name = os.environ.get("QWEN_MODEL", "qwen-plus")

    credential = DashScopeCredential(api_key=api_key)
    model = DashScopeChatModel(credential=credential, model=model_name)

    toolkit = Toolkit()
    ws = str(workspace.resolve())
    toolkit.add_tool(Read())
    toolkit.add_tool(Write())
    toolkit.add_tool(Edit())
    toolkit.add_tool(Bash(cwd=ws))
    toolkit.add_tool(Glob())
    toolkit.add_tool(Grep())

    system_prompt = textwrap.dedent(f"""\
        You are 千问办公 Agentic Coding Agent — an AI programming assistant
        similar to Qoder, Claude Code, and OpenAI Codex.

        Your working directory (workspace) is: {ws}

        RULES:
        - Use the provided tools to read, write, edit, search, and execute
          code in the workspace.
        - Always read a file before editing it.
        - After making changes, run a verification command (syntax check,
          tests, linter) when possible.
        - Prefer small, focused edits over full rewrites.
        - Explain what you did and show the result.
        - All file paths must be absolute.
        - Respond in the same language the user uses.
    """)

    react_config = ReActConfig(max_iters=20)

    agent = Agent(
        name="千问办公",
        system_prompt=system_prompt,
        model=model,
        toolkit=toolkit,
        react_config=react_config,
    )
    return agent


async def _agent_reply_async(agent, user_text: str):
    """Run agent.reply_stream and collect trajectory from streamed events."""
    from agentscope.message import Msg, TextBlock
    from agentscope.event import (
        ToolCallStartEvent,
        ToolCallEndEvent,
        ToolResultTextDeltaEvent,
        TextBlockDeltaEvent,
    )

    msg = Msg(name="user", content=[TextBlock(text=user_text)], role="user")

    tool_calls = []
    current_tool = None
    current_result_parts = []
    answer_parts = []

    async for evt in agent.reply_stream(msg, yield_final_msg=True):
        if isinstance(evt, ToolCallStartEvent):
            current_tool = {"name": evt.tool_call_name, "id": evt.tool_call_id, "args": ""}
        elif isinstance(evt, ToolCallEndEvent):
            if current_tool is not None:
                current_tool["result"] = "".join(current_result_parts)
                tool_calls.append(current_tool)
                current_tool = None
                current_result_parts = []
        elif isinstance(evt, ToolResultTextDeltaEvent):
            current_result_parts.append(evt.delta)
        elif isinstance(evt, TextBlockDeltaEvent):
            answer_parts.append(evt.delta)
        elif isinstance(evt, Msg):
            final_text = evt.get_text_content() if hasattr(evt, "get_text_content") else str(evt)
            if final_text:
                answer_parts.append(final_text)

    final_answer = "".join(answer_parts).strip() or "(agent produced no text output)"
    return final_answer, tool_calls


def _run_agent_task(workspace: Path, task: str) -> None:
    """Run a single task through the real AgentScope agent."""
    agent = _build_agent(workspace)
    print(f'\n🤖 Agent: processing task …\n   "{task}"\n')

    final_answer, tool_calls = asyncio.run(_agent_reply_async(agent, task))

    # Print trajectory
    if tool_calls:
        print()
        print("=" * 64)
        print("  🔧  Tool-Call Trajectory")
        print("=" * 64)
        for i, tc in enumerate(tool_calls, 1):
            print(f"\n  [{i}] {tc['name']}")
            args_str = tc.get("args", "")
            if args_str:
                preview = args_str if len(args_str) < 200 else args_str[:197] + "..."
                print(f"      args: {preview}")
            result = tc.get("result", "")
            if result:
                preview = result if len(result) < 300 else result[:297] + "..."
                print(f"      → {preview}")
        print()

    print("-" * 64)
    print("  💬  Final Answer")
    print("-" * 64)
    print(f"  {final_answer}")
    print("=" * 64)
    print()


# ---------------------------------------------------------------------------
# REPL — interactive coding session
# ---------------------------------------------------------------------------

def _run_repl(workspace: Path) -> None:
    """Interactive REPL: each input is a coding task for the agent."""
    agent = _build_agent(workspace)
    print("=" * 64)
    print("  千问办公 — Agentic Coding Agent")
    print("=" * 64)
    print(f"  Workspace : {workspace.resolve()}")
    print(f"  Model     : {os.environ.get('QWEN_MODEL', 'qwen-plus')}")
    print()
    print("  Type a coding task and press Enter.")
    print("  Commands:  /quit  /exit  /tools  /workspace")
    print("-" * 64)
    print()

    while True:
        try:
            user_input = input("🧑‍💻 You > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit", "/q"):
            print("Bye!")
            break
        if user_input == "/tools":
            print("  Tools: read_file, write_file, edit_file, glob, grep, run_shell")
            continue
        if user_input == "/workspace":
            print(f"  {workspace.resolve()}")
            continue

        print()
        final_answer, tool_calls = asyncio.run(_agent_reply_async(agent, user_input))

        if tool_calls:
            for i, tc in enumerate(tool_calls, 1):
                print(f"  🔧 [{i}] {tc['name']}")
                result = tc.get("result", "")
                if result:
                    preview = result if len(result) < 200 else result[:197] + "..."
                    print(f"      → {preview}")
            print()

        print(f"  🤖 Agent > {final_answer}\n")


# ---------------------------------------------------------------------------
# Optional Web UI — Quest-style task window
# ---------------------------------------------------------------------------

_WEB_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>千问办公 — Agentic Coding Agent</title>
<style>
  :root { --bg:#0f1117; --fg:#e0e0e0; --accent:#6c63ff; --muted:#888; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family: 'SF Mono', 'Fira Code', monospace; background:var(--bg); color:var(--fg); display:flex; height:100vh; }
  #sidebar { width:260px; border-right:1px solid #222; padding:16px; display:flex; flex-direction:column; }
  #sidebar h1 { font-size:15px; margin-bottom:12px; color:var(--accent); }
  #sidebar .info { font-size:12px; color:var(--muted); margin-bottom:8px; }
  #main { flex:1; display:flex; flex-direction:column; }
  #messages { flex:1; overflow-y:auto; padding:20px; }
  .msg { margin-bottom:16px; max-width:85%%; }
  .msg.user { margin-left:auto; background:#1a1d2e; padding:10px 14px; border-radius:10px; }
  .msg.agent { background:#161922; padding:10px 14px; border-radius:10px; border-left:3px solid var(--accent); }
  .msg .tool { font-size:12px; color:var(--muted); margin-top:6px; }
  .msg .tool span { display:inline-block; background:#1e2130; padding:2px 6px; border-radius:4px; margin:2px; }
  #input-bar { display:flex; border-top:1px solid #222; padding:12px; }
  #input-bar input { flex:1; background:#1a1d2e; border:1px solid #333; color:var(--fg); padding:10px 14px; border-radius:8px; font-size:14px; outline:none; }
  #input-bar input:focus { border-color:var(--accent); }
  #input-bar button { margin-left:8px; background:var(--accent); border:none; color:#fff; padding:10px 18px; border-radius:8px; cursor:pointer; font-size:14px; }
  #input-bar button:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
  <div id="sidebar">
    <h1>千问办公</h1>
    <div class="info">Agentic Coding Agent</div>
    <div class="info">Workspace: __WORKSPACE__</div>
    <div class="info">Model: __MODEL__</div>
    <hr style="border-color:#222;margin:12px 0">
    <div class="info">Tools: read_file · write_file · edit_file · glob · grep · run_shell</div>
  </div>
  <div id="main">
    <div id="messages">
      <div class="msg agent">
        👋 Hi! I'm your coding agent. Describe a task and I'll read, write, and run code in your workspace.
      </div>
    </div>
    <div id="input-bar">
      <input id="task" placeholder="Describe a coding task…" autofocus>
      <button id="send" onclick="sendTask()">Send</button>
    </div>
  </div>
<script>
const WS = location.protocol === 'https:' ? 'wss' : 'ws';
let ws;
function connect() {
  ws = new WebSocket(WS + '://' + location.host + '/ws');
  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    const box = document.getElementById('messages');
    if (data.type === 'tool') {
      const el = document.querySelector('.msg.agent:last-child .tool');
      if (el) el.innerHTML += '<span>' + data.name + '</span>';
    } else if (data.type === 'answer') {
      const div = document.createElement('div');
      div.className = 'msg agent';
      div.textContent = data.text;
      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
      document.getElementById('send').disabled = false;
    } else if (data.type === 'error') {
      const div = document.createElement('div');
      div.className = 'msg agent';
      div.style.color = '#f66';
      div.textContent = 'Error: ' + data.text;
      box.appendChild(div);
      document.getElementById('send').disabled = false;
    }
  };
  ws.onclose = () => setTimeout(connect, 2000);
}
function sendTask() {
  const input = document.getElementById('task');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  document.getElementById('send').disabled = true;
  const box = document.getElementById('messages');
  const u = document.createElement('div');
  u.className = 'msg user';
  u.textContent = text;
  box.appendChild(u);
  const a = document.createElement('div');
  a.className = 'msg agent';
  a.innerHTML = '⏳ Working…<div class="tool"></div>';
  box.appendChild(a);
  box.scrollTop = box.scrollHeight;
  ws.send(JSON.stringify({task: text}));
}
document.getElementById('task').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendTask();
});
connect();
</script>
</body>
</html>"""


def _run_web(workspace: Path, port: int) -> None:
    """Start a lightweight web UI with WebSocket for the coding agent."""
    _load_agentscope()

    try:
        import aiohttp
        from aiohttp import web
    except ImportError:
        print(
            "Error: --web requires aiohttp.\n"
            "Run:  pip install aiohttp",
            file=sys.stderr,
        )
        sys.exit(1)

    agent = _build_agent(workspace)
    html = (
        _WEB_HTML
        .replace("__WORKSPACE__", str(workspace.resolve()))
        .replace("__MODEL__", os.environ.get("QWEN_MODEL", "qwen-plus"))
    )

    async def index(request):
        return web.Response(text=html, content_type="text/html")

    async def websocket_handler(request):
        ws_web = web.WebSocketResponse()
        await ws_web.prepare(request)

        async for raw in ws_web:
            if raw.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(raw.data)
                task = data.get("task", "")
                if not task:
                    continue

                try:
                    final_answer, tool_calls = await _agent_reply_async(agent, task)
                    for tc in tool_calls:
                        await ws_web.send_json({"type": "tool", "name": tc["name"]})
                    await ws_web.send_json({"type": "answer", "text": final_answer})
                except Exception as exc:
                    await ws_web.send_json({"type": "error", "text": str(exc)})

        return ws_web

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)

    print(f"\n🌐 千问办公 Web UI → http://localhost:{port}")
    print(f"    Workspace: {workspace.resolve()}\n")
    web.run_app(app, host="0.0.0.0", port=port, print=None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_dotenv(workspace: Path) -> None:
    """Load .env from workspace or cwd if present (no crash if missing)."""
    for candidate in [workspace / ".env", Path.cwd() / ".env"]:
        if candidate.is_file():
            try:
                from dotenv import load_dotenv
                load_dotenv(candidate)
            except ImportError:
                # manual parse
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            break


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="qianwen-office",
        description=(
            "qianwen-office — Agentic Coding Agent\n\n"
            "An AI coding agent (like Qoder / Claude Code / Codex) built with\n"
            "AgentScope 2.x + Qwen. Describe a task; the agent reads, writes,\n"
            "and runs code in your workspace."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              python app.py --demo                       # demo mode (no API key)
              python app.py "Add unit tests for utils"   # one-shot task
              python app.py                              # interactive REPL
              python app.py --web --port 8080            # web UI on :8080

            environment:
              DASHSCOPE_API_KEY   DashScope API key (required unless --demo)
              QWEN_MODEL          Model name (default: qwen-plus)
        """),
    )
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="A coding task to execute (omit for REPL)",
    )
    parser.add_argument(
        "--workspace", "-w",
        type=str,
        default=".",
        help="Workspace directory for the agent (default: current dir)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run demo mode — simulates a full coding loop without API calls",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Start the web UI (Quest-style task window)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        help="Port for web UI (default: 8000)",
    )

    args = parser.parse_args(argv)
    workspace = Path(args.workspace).resolve()

    # --demo short-circuits everything
    if args.demo:
        _run_demo(workspace)
        return

    # Load .env if available
    _load_dotenv(workspace)

    if args.web:
        _run_web(workspace, args.port)
    elif args.task:
        _run_agent_task(workspace, args.task)
    else:
        _run_repl(workspace)


if __name__ == "__main__":
    main()
