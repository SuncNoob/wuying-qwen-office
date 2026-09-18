"""Cowork CLI: auth, project create, bootstrap, task add, status."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from cowork import __version__
from cowork.bootstrap import doctor_agent, install_agent
from cowork.localcfg import load_config, password_from, save_config, targets_from
from cowork.monitor import serve as serve_monitor
from cowork.project import add_task, init_project
from cowork.protocol import Task, next_task_id, now
from cowork.sshutil import probe
from cowork.store import Store


def repo_root() -> Path:
    return Path.cwd()


def cmd_auth_ssh(args: argparse.Namespace) -> int:
    data = load_config()
    data.setdefault("ssh", {})
    data["ssh"]["host"] = args.host
    data["ssh"]["user"] = args.user
    data["ssh"]["become"] = args.become
    if args.password:
        data["ssh"]["password"] = args.password
    elif os.environ.get("COWORK_SSH_PASSWORD"):
        data["ssh"]["password"] = os.environ["COWORK_SSH_PASSWORD"]
    agents = []
    for spec in args.agent:
        agent_id, port, role = spec.split(":")
        agents.append(
            {
                "id": agent_id,
                "port": int(port),
                "role": role,
                "host": args.host,
                "user": args.user,
                "become": args.become,
            }
        )
    data["agents"] = agents
    path = save_config(data)
    print(f"wrote {path}")
    for target in targets_from(data):
        info = probe(target)
        print(f"ok {target.id} port={target.port} user={info['user']}")
    return 0


def cmd_auth_github(args: argparse.Namespace) -> int:
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr or "gh auth status failed. Run: gh auth login")
        return 1
    print(result.stdout or "github auth ok")
    data = load_config()
    data.setdefault("github", {})
    if args.remote:
        data["github"]["remote"] = args.remote
    save_config(data)
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    data = load_config()
    agents = data.get("agents") or []
    if not agents:
        raise SystemExit("no agents in local config. Run: cowork auth ssh ...")
    if args.scenario == "all":
        # dual-cap: each machine keeps its primary role; extra capabilities added below
        scenario = "all"
        extra = {
            "planner": ["enqueue"],
            "builder": ["fetch"],
            "reviewer": ["extract"],
            "dispatcher": ["plan"],
            "fetcher": ["implement"],
            "analyst": ["review"],
        }
    else:
        scenario = args.scenario
        extra = {}
    store = init_project(
        repo_root(),
        name=args.name,
        scenario=scenario,
        agents=agents,
        remote=args.remote or (data.get("github") or {}).get("remote", ""),
    )
    if extra:
        for card in store.all_agents().values():
            more = extra.get(card.role) or []
            card.capabilities = sorted(set(card.capabilities + more))
            store.save_agent(card)
        project = store.load_project()
        project["scenario"] = "all"
        store.save_project(project)
    print(f"project {args.name} scenario={store.load_project().get('scenario')} agents={len(agents)}")
    return 0


def cmd_task_add(args: argparse.Namespace) -> int:
    store = Store(repo_root())
    store.ensure_layout()
    task_id = args.id or next_task_id(store.all_tasks())
    seed_urls = args.url or []
    allow_hosts = args.allow_host or []
    task = Task(
        id=task_id,
        scenario=args.scenario,
        type=args.type,
        title=args.title,
        body=args.body or "",
        path=args.path or "",
        content=args.content or "",
        seed_urls=seed_urls,
        allow_hosts=allow_hosts,
        mode=getattr(args, "mode", "") or "http",
        brand=getattr(args, "brand", "") or "",
        max_images=int(getattr(args, "max_images", 0) or 0),
        status="open",
        created_at=now(),
        updated_at=now(),
    )
    add_task(store, task)
    print(task.id)
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    data = load_config()
    targets = targets_from(data)
    if not targets:
        raise SystemExit("no agents configured")
    remote = args.remote or (data.get("github") or {}).get("remote") or ""
    key = None
    key_path = Path.home() / ".config" / "cowork" / "cowork_deploy"
    if key_path.exists():
        key = key_path.read_text(encoding="utf-8")
    for target in targets:
        print(f"install {target.id} ...")
        pid = install_agent(
            target,
            repo_root(),
            git_remote=remote,
            deploy_key=key,
            start=not args.no_start,
        )
        print(f"  pid {pid}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = Store(repo_root())
    project = store.load_project()
    print(json.dumps(project, ensure_ascii=False, indent=2))
    print("--- agents ---")
    for card in store.all_agents().values():
        print(f"{card.id:8} role={card.role:12} caps={card.capabilities} hb={card.heartbeat_at}")
    print("--- tasks ---")
    for task in store.all_tasks().values():
        print(f"{task.id} {task.status:12} {task.type:10} owner={task.owner or '-'} {task.title}")
    if args.remote:
        data = load_config()
        for target in targets_from(data):
            info = doctor_agent(target)
            print(f"--- {target.id} ---")
            print(info["out"] or info["err"])
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    serve_monitor(host=args.host, port=args.port, open_browser=args.open, root=args.root or None)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    data = load_config()
    ok = True
    if not password_from(data) and not (data.get("agents")):
        print("no local ssh config")
        ok = False
    for target in targets_from(data):
        try:
            info = probe(target)
            print(f"ssh ok {target.id} {info['user']}")
        except Exception as exc:
            ok = False
            print(f"ssh FAIL {target.id}: {exc}")
    gh = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if gh.returncode == 0:
        print("github ok")
    else:
        print("github FAIL")
        ok = False
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cowork", description="无影 Agentic Computer multi-agent cowork")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="self-service credentials")
    auth_sub = auth.add_subparsers(dest="auth_cmd", required=True)
    ssh = auth_sub.add_parser("ssh")
    ssh.add_argument("--host", default="8.139.215.238")
    ssh.add_argument("--user", default="root")
    ssh.add_argument("--become", default="admin")
    ssh.add_argument("--password", default="")
    ssh.add_argument(
        "--agent",
        action="append",
        required=True,
        help="id:port:role  e.g. ac1:56735:planner",
    )
    ssh.set_defaults(func=cmd_auth_ssh)
    gh = auth_sub.add_parser("github")
    gh.add_argument("--remote", default="")
    gh.set_defaults(func=cmd_auth_github)

    create = sub.add_parser("project")
    create_sub = create.add_subparsers(dest="project_cmd", required=True)
    pc = create_sub.add_parser("create")
    pc.add_argument("--name", default="cowork")
    pc.add_argument("--scenario", choices=["coding", "research", "crawler", "all"], default="all")
    pc.add_argument("--remote", default="")
    pc.set_defaults(func=cmd_project_create)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)
    ta = task_sub.add_parser("add")
    ta.add_argument("--id", default="")
    ta.add_argument("--scenario", required=True, choices=["coding", "research", "crawler"])
    ta.add_argument("--type", required=True)
    ta.add_argument("--title", required=True)
    ta.add_argument("--body", default="")
    ta.add_argument("--path", default="")
    ta.add_argument("--content", default="")
    ta.add_argument("--url", action="append")
    ta.add_argument("--allow-host", action="append")
    ta.add_argument("--mode", default="http", choices=["http", "browser", "research", "codex"])
    ta.add_argument("--brand", default="")
    ta.add_argument("--max-images", type=int, default=0)
    ta.set_defaults(func=cmd_task_add)

    boot = sub.add_parser("bootstrap")
    boot.add_argument("--remote", default="")
    boot.add_argument("--no-start", action="store_true")
    boot.set_defaults(func=cmd_bootstrap)

    st = sub.add_parser("status")
    st.add_argument("--remote", action="store_true")
    st.set_defaults(func=cmd_status)

    mon = sub.add_parser("monitor", help="local web dashboard for 3 Agentic Computers")
    mon.add_argument("--host", default="127.0.0.1")
    mon.add_argument("--port", type=int, default=8765)
    mon.add_argument("--root", default="", help="local cowork bus repo (default: infer)")
    mon.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    mon.set_defaults(func=cmd_monitor)

    doc = sub.add_parser("doctor")
    doc.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
