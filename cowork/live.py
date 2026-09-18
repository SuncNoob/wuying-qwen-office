"""Live Agentic Computer snapshot and task dispatch for the local monitor."""

from __future__ import annotations

import base64
import json
import re
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from cowork.codex_run import CODEX_LEASE_SECONDS, uses_codex_session
from cowork.describe import describe_task
from cowork.localcfg import config_path, load_config, targets_from
from cowork.protocol import DEFAULT_LEASE_SECONDS, Task, make_claim, now
from cowork.sshutil import Remote, SSHTarget
from cowork.store import GitError, Store

UNFINISHED_STATUSES = {"open", "failed", "claimed"}
ACTIVE_STATUSES = {"claimed", "in_progress"}
STATUS_RANK = {
    "in_progress": 4,
    "claimed": 3,
    "failed": 2,
    "open": 1,
    "done": 0,
    "invalid": -1,
}
BRAND_RE = re.compile(r"Brand:\s+(\S+)")
URL_RE = re.compile(r"Official (?:start|listing) URL:\s+(\S+)")

SNAPSHOT_PY = r"""
import json, pathlib, subprocess, time
workdir = WORKDIR
agent_id = AGENT_ID
out = {
    "id": agent_id,
    "online": True,
    "error": "",
    "cwd": workdir,
    "heartbeat_at": 0,
    "role": "",
    "capabilities": [],
    "runtime": None,
    "codex": None,
    "tasks": [],
    "pics": [],
    "pic_counts": {},
    "git_head": "",
    "git_status": "",
}
try:
    ps = subprocess.run(
        ["ps", "-u", "admin", "-o", "pid,etime,cmd"],
        capture_output=True, text=True, timeout=8,
    )
    out["ps"] = ps.stdout
except Exception as exc:
    out["ps"] = ""
    out["error"] = str(exc)
root = pathlib.Path(workdir)
try:
    head = subprocess.run(
        ["git", "log", "-1", "--format=%h %s"],
        cwd=workdir, capture_output=True, text=True, timeout=8,
    )
    out["git_head"] = (head.stdout or "").strip()
    st = subprocess.run(
        ["git", "status", "-sb"],
        cwd=workdir, capture_output=True, text=True, timeout=8,
    )
    out["git_status"] = (st.stdout or "").splitlines()[0] if st.stdout else ""
except Exception:
    pass
tasks_dir = root / ".cowork" / "tasks"
if tasks_dir.is_dir():
    for path in sorted(tasks_dir.glob("TASK-*.json")):
        try:
            out["tasks"].append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            out["tasks"].append({"id": path.stem, "status": "invalid", "title": path.name})
agent_path = root / ".cowork" / "agents" / f"{agent_id}.json"
if agent_path.exists():
    try:
        card = json.loads(agent_path.read_text(encoding="utf-8"))
        out["role"] = card.get("role") or ""
        out["capabilities"] = card.get("capabilities") or []
        out["heartbeat_at"] = int(card.get("heartbeat_at") or 0)
    except Exception:
        pass
pics = root / "pics"
image_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
if pics.is_dir():
    counts = {}
    for path in sorted(pics.rglob("*")):
        if not path.is_file() or path.name in {".gitkeep", "manifest.json"}:
            continue
        out["pics"].append(str(path.relative_to(root)))
        if path.suffix.lower() not in image_ext:
            continue
        brand = path.parent.name if path.parent != pics else "_root"
        counts[brand] = counts.get(brand, 0) + 1
    out["pic_counts"] = counts
print(json.dumps(out, ensure_ascii=False))
"""

DISPATCH_PY = r"""
import json, pathlib, sys, time
from cowork.codex_run import CODEX_LEASE_SECONDS
from cowork.protocol import DEFAULT_LEASE_SECONDS, make_claim, now
from cowork.store import GitError, Store

task_id = TASK_ID
agent_id = AGENT_ID
reopen_only = REOPEN_ONLY
force = FORCE
store = Store(pathlib.Path(WORKDIR))
store.ensure_layout()
try:
    store.pull()
except GitError:
    pass
task = store.load_task(task_id)
if task is None:
    print(json.dumps({"ok": False, "error": f"missing {task_id}"}))
    raise SystemExit(0)
if task.status == "done" and not force:
    print(json.dumps({"ok": False, "error": f"{task_id} already done"}))
    raise SystemExit(0)
if task.status == "in_progress" and task.owner and task.owner != agent_id and not force and not reopen_only:
    print(json.dumps({"ok": False, "error": f"{task_id} in_progress on {task.owner}"}))
    raise SystemExit(0)
if reopen_only or not agent_id:
    task.status = "open"
    task.owner = ""
    store.clear_claim(task_id)
    store.save_task(task)
    msg = f"cowork(monitor): reopen {task_id}"
else:
    lease = CODEX_LEASE_SECONDS if (task.mode or "") in ("browser", "research", "codex") else DEFAULT_LEASE_SECONDS
    task.status = "claimed"
    task.owner = agent_id
    store.save_task(task)
    store.save_claim(make_claim(task_id, agent_id, lease_seconds=lease))
    msg = f"cowork(monitor): dispatch {task_id} -> {agent_id}"
try:
    store.sync_push(msg)
except GitError as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
    raise SystemExit(0)
wake = store.inbox_dir(agent_id) / "wake" if agent_id else None
if wake is not None:
    wake.write_text(str(int(time.time())), encoding="utf-8")
print(json.dumps({"ok": True, "task_id": task_id, "agent_id": agent_id, "status": task.status}))
"""


def parse_ps(text: str) -> dict[str, Any]:
    runtime = None
    codex = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etime, cmd = parts
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if "cowork.runtime" in cmd and "grep" not in cmd:
            runtime = {"pid": pid, "etime": etime, "alive": True}
        elif "/codex exec" in cmd or cmd.startswith("codex exec") or "codex exec " in cmd:
            brand_m = BRAND_RE.search(cmd)
            url_m = URL_RE.search(cmd)
            codex = {
                "pid": pid,
                "etime": etime,
                "alive": True,
                "brand": brand_m.group(1) if brand_m else "",
                "url": url_m.group(1) if url_m else "",
            }
    return {"runtime": runtime, "codex": codex}


def repo_name_from_remote(remote: str) -> str:
    name = (remote or "").rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def bus_root(cfg: dict[str, Any], explicit: str | Path | None = None) -> Path:
    """Resolve the local Git bus clone from --root, then github.remote, then cwd.

    IP/port live in laptop config and can change; the bus directory follows the
    current remote name (jewelry vs research) instead of a hardcoded clone.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    cwd = Path.cwd()
    repo = Path(__file__).resolve().parent.parent
    remote = (cfg.get("github") or {}).get("remote") or ""
    name = repo_name_from_remote(remote)
    candidates: list[Path] = []
    if name:
        candidates.extend(
            [
                Path.home() / name,
                repo.parent / name,
                cwd / name,
                cwd.parent / name,
            ]
        )
    candidates.append(cwd)
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / ".cowork" / "project.json").exists():
            return resolved
    return cwd


def unfinished(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tasks if str(t.get("status") or "") in UNFINISHED_STATUSES]


def task_ready(task: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    for dep in task.get("depends_on") or []:
        parent = by_id.get(dep) or {}
        if parent.get("status") != "done":
            return False
    return True


def agent_busy(agent: dict[str, Any]) -> bool:
    if agent.get("codex"):
        return True
    current = agent.get("current_task") or {}
    return current.get("status") in {"claimed", "in_progress"}


def choose_assignee(task: dict[str, Any], agents: list[dict[str, Any]], idle_only: bool = True) -> str:
    kind = str(task.get("type") or "")
    candidates = []
    for agent in agents:
        if not agent.get("online"):
            continue
        caps = agent.get("capabilities") or []
        if kind and kind not in caps:
            continue
        if idle_only and agent_busy(agent):
            continue
        candidates.append(agent)
    if not candidates:
        return ""
    candidates.sort(key=lambda a: (agent_busy(a), a.get("id") or ""))
    return str(candidates[0]["id"])


def _current_task(agent_id: str, tasks: list[dict[str, Any]], codex: dict[str, Any] | None) -> dict[str, Any] | None:
    owned = [
        t
        for t in tasks
        if t.get("owner") == agent_id and t.get("status") in ACTIVE_STATUSES
    ]
    owned.sort(key=lambda t: 0 if t.get("status") == "in_progress" else 1)
    if owned:
        return owned[0]
    if not codex:
        return None
    brand = (codex.get("brand") or "").lower()
    if not brand:
        return None
    for t in tasks:
        if str(t.get("brand") or "").lower() == brand and t.get("status") in ACTIVE_STATUSES:
            return t
    return None


def merge_task_board(
    agents: list[dict[str, Any]],
    extra_tasks: list[dict[str, Any]] | None = None,
    extra_source: str = "本地仓库",
) -> tuple[list[dict[str, Any]], str]:
    """Pick the newest copy of each task across online ACs and the laptop clone."""
    best: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
    sources: list[str] = []
    rows = list(agents)
    if extra_tasks:
        rows.append({"id": extra_source, "online": True, "tasks": extra_tasks})
    for agent in rows:
        if not agent.get("online"):
            continue
        copies = list(agent.get("tasks") or [])
        if copies:
            sources.append(str(agent.get("id") or ""))
        for task in copies:
            tid = str(task.get("id") or "")
            if not tid:
                continue
            score = (
                int(task.get("updated_at") or 0),
                STATUS_RANK.get(str(task.get("status") or ""), 0),
            )
            prev = best.get(tid)
            if prev is None or score >= prev[0]:
                best[tid] = (score, dict(task))
    tasks = [best[key][1] for key in sorted(best)]
    source = "、".join(sources) if sources else "本地仓库"
    return tasks, source


def annotate_tasks(tasks: list[dict[str, Any]], pic_counts: dict[str, int] | None = None) -> None:
    by_id = {str(t.get("id")): t for t in tasks}
    counts = pic_counts or {}
    for task in tasks:
        task["description"] = describe_task(task)
        blocked = [
            dep
            for dep in (task.get("depends_on") or [])
            if (by_id.get(dep) or {}).get("status") != "done"
        ]
        ready = not blocked
        task["ready"] = ready
        task["blocked_by"] = blocked
        status = str(task.get("status") or "")
        if status == "open":
            task["status_label"] = "可下发" if ready else "等待上游"
            task["status_kind"] = "ready" if ready else "blocked"
        elif status == "in_progress":
            task["status_label"] = "执行中"
            task["status_kind"] = "in_progress"
        elif status == "claimed":
            task["status_label"] = "已领取"
            task["status_kind"] = "claimed"
        elif status == "failed":
            task["status_label"] = "失败"
            task["status_kind"] = "failed"
        elif status == "done":
            task["status_label"] = "已完成"
            task["status_kind"] = "done"
        else:
            task["status_label"] = status or "未知"
            task["status_kind"] = status or "unknown"
        brand = str(task.get("brand") or "")
        target = int(task.get("max_images") or 0)
        if brand and target:
            task["images_done"] = int(counts.get(brand, 0))
            task["images_target"] = target


def brand_image_counts(agents: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for agent in agents:
        for brand, n in (agent.get("pic_counts") or {}).items():
            counts[str(brand)] = max(counts.get(str(brand), 0), int(n or 0))
    return counts


def snapshot_summary(agents: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "agents": len(agents),
        "online": sum(1 for a in agents if a.get("online")),
        "running": sum(1 for a in agents if a.get("codex")),
        "idle": sum(1 for a in agents if a.get("online") and not agent_busy(a)),
        "done": sum(1 for t in tasks if t.get("status") == "done"),
        "failed": sum(1 for t in tasks if t.get("status") == "failed"),
        "running_tasks": sum(1 for t in tasks if t.get("status") in ACTIVE_STATUSES),
        "ready": sum(
            1
            for t in tasks
            if str(t.get("status") or "") in UNFINISHED_STATUSES and t.get("ready")
        ),
        "blocked": sum(1 for t in tasks if t.get("status_kind") == "blocked"),
    }


def _b64_exec(script: str, replacements: dict[str, Any]) -> str:
    text = script
    for key, value in replacements.items():
        if isinstance(value, bool):
            text = text.replace(key, "True" if value else "False")
        else:
            text = text.replace(key, json.dumps(value))
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    inner = "python3 -c \"import base64; exec(base64.b64decode('" + encoded + "').decode())\""
    workdir = replacements.get("WORKDIR")
    if isinstance(workdir, str) and workdir:
        return f"cd {shlex.quote(workdir)} && PYTHONPATH=. {inner}"
    return inner


def endpoint_fields(target: SSHTarget) -> dict[str, Any]:
    return {
        "ssh_host": target.host,
        "ssh_port": target.port,
        "ssh_user": target.user,
        "workdir": target.workdir,
        "cwd": target.workdir,
        "endpoint": f"{target.user}@{target.host}:{target.port}",
    }


def load_bus_tasks(root: Path) -> list[dict[str, Any]]:
    if not (root / ".cowork" / "tasks").exists() and not (root / ".cowork" / "project.json").exists():
        return []
    store = Store(root)
    try:
        store.pull()
    except GitError:
        pass
    return [task.to_dict() for task in store.all_tasks().values()]


def probe_agent(target: SSHTarget) -> dict[str, Any]:
    fallback = {
        "id": target.id,
        "online": False,
        "error": "",
        "role": "",
        "capabilities": [],
        "heartbeat_at": 0,
        "runtime": None,
        "codex": None,
        "current_task": None,
        "busy": False,
        "pics": [],
        "pic_counts": {},
        "git_head": "",
        "git_status": "",
        "tasks": [],
        **endpoint_fields(target),
    }
    if not target.host or not target.port:
        fallback["error"] = "ssh host/port 未配置，请改本地配置后刷新（不必重启监控）"
        return fallback
    try:
        with Remote(target) as remote:
            cmd = _b64_exec(SNAPSHOT_PY, {"WORKDIR": target.workdir, "AGENT_ID": target.id})
            code, out, err = remote.run(cmd, timeout=25)
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback
    payload = out.strip().splitlines()
    raw = ""
    for line in reversed(payload):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            raw = line
            break
    if not raw:
        fallback["error"] = (err or out or f"exit {code}")[:400]
        return fallback
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fallback["error"] = "invalid snapshot json"
        return fallback
    parsed = parse_ps(data.get("ps") or "")
    tasks = list(data.get("tasks") or [])
    data["runtime"] = parsed["runtime"]
    data["codex"] = parsed["codex"]
    data["current_task"] = _current_task(target.id, tasks, parsed["codex"])
    data["busy"] = bool(parsed["codex"]) or (
        (data["current_task"] or {}).get("status") in {"claimed", "in_progress"}
    )
    data["online"] = True
    data["error"] = data.get("error") or ""
    data.update(endpoint_fields(target))
    if not data.get("runtime"):
        data["error"] = data["error"] or "coworkd not running"
    return data


def collect_snapshot(cfg: dict[str, Any] | None = None, root: Path | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    targets = targets_from(cfg)
    agents: list[dict[str, Any]] = []
    errors: list[str] = []
    if targets:
        with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
            futs = {pool.submit(probe_agent, t): t.id for t in targets}
            by_id: dict[str, dict[str, Any]] = {}
            for fut in as_completed(futs):
                by_id[futs[fut]] = fut.result()
            agents = [by_id[t.id] for t in targets]
    bus = root or bus_root(cfg)
    local_tasks = load_bus_tasks(bus)
    tasks, task_source = merge_task_board(agents, extra_tasks=local_tasks)
    if (bus / ".cowork" / "project.json").exists():
        project = Store(bus).load_project()
    else:
        project = {"name": Path(targets[0].workdir).name if targets else "cowork"}
    pic_counts = brand_image_counts(agents)
    annotate_tasks(tasks, pic_counts)
    for agent in agents:
        current = _current_task(str(agent.get("id") or ""), tasks, agent.get("codex"))
        if current:
            current = dict(current)
            current["description"] = describe_task(current)
        agent["current_task"] = current
        agent["busy"] = agent_busy(agent)
        if agent.get("heartbeat_at"):
            agent["heartbeat_age_s"] = max(0, now() - int(agent["heartbeat_at"]))
        else:
            agent["heartbeat_age_s"] = None
        age = agent.get("heartbeat_age_s")
        if agent.get("codex") and age is not None and age > 90:
            agent["heartbeat_note"] = "长任务执行中，心跳暂不更新属正常"
        elif age is None:
            agent["heartbeat_note"] = "尚未上报心跳"
        else:
            agent["heartbeat_note"] = ""
        brand = str((current or {}).get("brand") or (agent.get("codex") or {}).get("brand") or "")
        agent["brand"] = brand
        agent["brand_images"] = int((agent.get("pic_counts") or {}).get(brand, 0)) if brand else 0
        agent["images_target"] = int((current or {}).get("max_images") or 0)
        cx_brand = str((agent.get("codex") or {}).get("brand") or "").lower()
        task_brand = str((current or {}).get("brand") or "").lower()
        if cx_brand and task_brand and cx_brand != task_brand:
            agent["mismatch"] = f"Codex 在抓 {cx_brand}，任务板是 {(current or {}).get('id')}"
        else:
            agent["mismatch"] = ""
        err = str(agent.get("error") or "")
        if err and (not agent.get("online") or "coworkd not running" not in err):
            errors.append(f"{agent.get('id')}: {err}")
    ssh = cfg.get("ssh") or {}
    return {
        "generated_at": now(),
        "project": project,
        "root": str(bus),
        "config_path": str(config_path()),
        "ssh_host": ssh.get("host") or "",
        "task_source": task_source,
        "agents": agents,
        "tasks": tasks,
        "unfinished": unfinished(tasks),
        "summary": snapshot_summary(agents, tasks),
        "errors": errors,
    }


def _target_map(cfg: dict[str, Any]) -> dict[str, SSHTarget]:
    return {t.id: t for t in targets_from(cfg)}


def _run_dispatch(target: SSHTarget, task_id: str, agent_id: str, reopen_only: bool, force: bool) -> dict[str, Any]:
    cmd = _b64_exec(
        DISPATCH_PY,
        {
            "WORKDIR": target.workdir,
            "TASK_ID": task_id,
            "AGENT_ID": agent_id,
            "REOPEN_ONLY": reopen_only,
            "FORCE": force,
        },
    )
    with Remote(target) as remote:
        code, out, err = remote.run(cmd, timeout=90)
    raw = ""
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            raw = line
            break
    if not raw:
        return {"ok": False, "error": (err or out or f"exit {code}")[:500]}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid dispatch json"}
    return data


def dispatch_tasks(
    body: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    snapshot = snapshot if snapshot is not None else collect_snapshot(cfg)
    agents = list(snapshot.get("agents") or [])
    tasks = list(snapshot.get("tasks") or [])
    by_id = {str(t.get("id")): t for t in tasks}
    force = bool(body.get("force"))
    reopen_only = bool(body.get("reopen"))
    wanted: list[str] = []
    if body.get("unfinished"):
        for task in unfinished(tasks):
            if task_ready(task, by_id) or force:
                wanted.append(str(task["id"]))
        if not wanted:
            return {"ok": True, "results": [], "message": "没有可下发的未完成任务"}
    elif body.get("task_id"):
        wanted = [str(body["task_id"])]
    else:
        return {"ok": False, "error": "task_id or unfinished required"}
    targets = _target_map(cfg)
    if not targets:
        return {"ok": False, "error": "no agents in local config"}

    results = []
    assigned_now: set[str] = set()
    for task_id in wanted:
        task = by_id.get(task_id)
        if task is None:
            results.append({"task_id": task_id, "ok": False, "error": "unknown task"})
            continue
        agent_id = str(body.get("agent_id") or "")
        if reopen_only:
            agent_id = agent_id or next((a["id"] for a in agents if a.get("online")), next(iter(targets)))
            runner = targets.get(agent_id)
            if runner is None:
                results.append({"task_id": task_id, "ok": False, "error": "no ssh target"})
                continue
            result = _run_dispatch(runner, task_id, "", True, force)
            results.append(result)
            continue
        if not agent_id:
            idle_agents = [
                a for a in agents if a.get("id") not in assigned_now
            ]
            agent_id = choose_assignee(task, idle_agents, idle_only=True)
            if not agent_id:
                agent_id = choose_assignee(task, agents, idle_only=False)
        if not agent_id:
            results.append({"task_id": task_id, "ok": False, "error": "no capable agent"})
            continue
        runner = targets.get(agent_id) or targets.get(next(iter(targets)))
        if runner is None:
            results.append({"task_id": task_id, "ok": False, "error": "no ssh target"})
            continue
        result = _run_dispatch(runner, task_id, agent_id, False, force)
        if result.get("ok"):
            assigned_now.add(agent_id)
            for agent in agents:
                if agent.get("id") == agent_id:
                    agent["busy"] = True
        results.append(result)
    fail_n = sum(1 for r in results if not r.get("ok"))
    ok_n = len(results) - fail_n
    return {
        "ok": fail_n == 0,
        "ok_count": ok_n,
        "fail_count": fail_n,
        "results": results,
    }


def assign_local(store: Store, task_id: str, agent_id: str, reopen_only: bool = False) -> Task:
    """Pure local assign used by tests. Does not SSH."""
    task = store.load_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if reopen_only:
        task.status = "open"
        task.owner = ""
        store.clear_claim(task_id)
        store.save_task(task)
        return task
    lease = CODEX_LEASE_SECONDS if uses_codex_session(task.mode) else DEFAULT_LEASE_SECONDS
    task.status = "claimed"
    task.owner = agent_id
    store.save_task(task)
    store.save_claim(make_claim(task_id, agent_id, lease_seconds=lease))
    return task
