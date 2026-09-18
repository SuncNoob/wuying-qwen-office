"""Install cowork runtime onto 无影 AC over SSH and start coworkd."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from cowork.sshutil import Remote, SSHTarget

SKIP_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__", ".mypy_cache"}
SKIP_FILES = {"cowork.local.json"}


def pack_payload(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if rel.name in SKIP_FILES or rel.name.endswith(".pyc"):
                continue
            tar.add(path, arcname=str(rel))
    return buf.getvalue()


def install_agent(
    target: SSHTarget,
    repo_root: Path,
    git_remote: str,
    deploy_key: str | None = None,
    start: bool = True,
) -> str:
    workdir = target.workdir
    tar_path = f"/root/cowork-{target.id}.tgz"
    with Remote(target) as remote:
        remote.run_root(
            f"mkdir -p /home/admin/.ssh /home/admin/logs {workdir} && "
            f"chown -R {target.become}:{target.become} /home/admin/.ssh /home/admin/logs"
        )
        if deploy_key:
            remote.put_bytes(
                deploy_key.encode("utf-8"),
                "/home/admin/.ssh/cowork_deploy",
                mode=0o600,
            )
        jewelry_key_path = Path.home() / ".config" / "cowork" / "jewelry_deploy"
        if jewelry_key_path.exists():
            remote.put_bytes(
                jewelry_key_path.read_text(encoding="utf-8").encode("utf-8"),
                "/home/admin/.ssh/jewelry_deploy",
                mode=0o600,
            )
        research_key_path = Path.home() / ".config" / "cowork" / "research_deploy"
        if research_key_path.exists():
            remote.put_bytes(
                research_key_path.read_text(encoding="utf-8").encode("utf-8"),
                "/home/admin/.ssh/research_deploy",
                mode=0o600,
            )
        office_key_path = Path.home() / ".config" / "cowork" / "office_deploy"
        if office_key_path.exists():
            remote.put_bytes(
                office_key_path.read_text(encoding="utf-8").encode("utf-8"),
                "/home/admin/.ssh/office_deploy",
                mode=0o600,
            )
        ssh_config = (
            "Host github.com\n"
            "  HostName github.com\n"
            "  IdentityFile ~/.ssh/cowork_deploy\n"
            "  IdentitiesOnly yes\n"
            "  StrictHostKeyChecking accept-new\n"
            "\n"
            "Host github-jewelry\n"
            "  HostName github.com\n"
            "  IdentityFile ~/.ssh/jewelry_deploy\n"
            "  IdentitiesOnly yes\n"
            "  StrictHostKeyChecking accept-new\n"
            "\n"
            "Host github-research\n"
            "  HostName github.com\n"
            "  IdentityFile ~/.ssh/research_deploy\n"
            "  IdentitiesOnly yes\n"
            "  StrictHostKeyChecking accept-new\n"
            "\n"
            "Host github-office\n"
            "  HostName github.com\n"
            "  IdentityFile ~/.ssh/office_deploy\n"
            "  IdentitiesOnly yes\n"
            "  StrictHostKeyChecking accept-new\n"
        )
        remote.put_bytes(ssh_config.encode("utf-8"), "/home/admin/.ssh/config", mode=0o600)
        git_ssh = "GIT_SSH_COMMAND='ssh -F /home/admin/.ssh/config -o StrictHostKeyChecking=accept-new'"
        cloned = False
        if git_remote:
            clone = (
                f"rm -rf {workdir} && "
                f"{git_ssh} git clone {git_remote} {workdir}"
            )
            code, out, err = remote.run(clone, timeout=180)
            cloned = code == 0
            if not cloned:
                print(f"clone failed on {target.id}: {err or out}")
        if not cloned:
            remote.put_bytes(pack_payload(repo_root), tar_path, mode=0o644)
            code, out, err = remote.run_root(
                f"rm -rf {workdir} && mkdir -p {workdir} && tar -xzf {tar_path} -C {workdir} && "
                f"chown -R {target.become}:{target.become} {workdir}"
            )
            if code != 0:
                raise RuntimeError(err or out)
        git_setup = (
            f"cd {workdir} && "
            f"git config user.name {target.id}; "
            f"git config user.email {target.id}@cowork.local; "
        )
        if git_remote:
            git_setup += (
                f"git remote set-url origin {git_remote} 2>/dev/null || "
                f"(git init && git remote add origin {git_remote} && git branch -M main); "
            )
        code, out, err = remote.run(git_setup, timeout=60)
        if start:
            pidfile = f"/home/admin/logs/coworkd-{target.id}.pid"
            logfile = f"/home/admin/logs/coworkd-{target.id}.log"
            starter = f"""
python3 - <<'PY'
import os, subprocess, pathlib
pathlib.Path("/home/admin/logs").mkdir(parents=True, exist_ok=True)
pid_path = pathlib.Path("{pidfile}")
if pid_path.exists():
    try:
        os.kill(int(pid_path.read_text().strip()), 15)
    except Exception:
        pass
proc = subprocess.Popen(
    ["bash", "-lc", "exec python3 -m cowork.runtime --agent-id {target.id} --interval 12 >> {logfile} 2>&1"],
    cwd="{workdir}",
    env={{**os.environ, "PYTHONPATH": ".", "HOME": "/home/admin", "USER": "admin", "LOGNAME": "admin"}},
    stdin=subprocess.DEVNULL,
    start_new_session=True,
    user="{target.become}",
)
pid_path.write_text(str(proc.pid) + "\\n")
os.chown(pid_path, 1000, 1000)
print(proc.pid)
PY
"""
            code, out, err = remote.run_root(starter, timeout=20)
            pid = "".join(ch for ch in out if ch.isdigit())
            if not pid:
                raise RuntimeError(err or out or "failed to start coworkd")
            return pid
    return "installed"


def doctor_agent(target: SSHTarget) -> dict[str, str]:
    with Remote(target) as remote:
        code, out, err = remote.run(
            f"whoami; test -d {target.workdir} && echo HAS_WORKDIR; "
            f"test -f /home/admin/logs/coworkd-{target.id}.pid && echo PID=$(cat /home/admin/logs/coworkd-{target.id}.pid); "
            f"tail -n 30 /home/admin/logs/coworkd-{target.id}.log 2>/dev/null || true"
        )
        return {"code": str(code), "out": out, "err": err}
