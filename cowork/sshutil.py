"""SSH helpers for 无影 Agentic Computer boxes."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import paramiko


@dataclass
class SSHTarget:
    id: str
    host: str
    port: int
    user: str = "root"
    become: str = "admin"
    password: str = ""
    workdir: str = "/home/admin/cowork-bus"


class Remote:
    def __init__(self, target: SSHTarget):
        self.target = target
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            target.host,
            port=target.port,
            username=target.user,
            password=target.password or None,
            timeout=25,
            allow_agent=False,
            look_for_keys=False,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Remote":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run(self, command: str, timeout: int = 60, as_admin: bool = True) -> tuple[int, str, str]:
        if as_admin and self.target.become:
            wrapped = f"su - {self.target.become} -c {shlex.quote(command)}"
        else:
            wrapped = command
        stdin, stdout, stderr = self.client.exec_command(wrapped, timeout=timeout)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err

    def run_root(self, command: str, timeout: int = 60) -> tuple[int, str, str]:
        return self.run(command, timeout=timeout, as_admin=False)

    def put_bytes(self, data: bytes, remote_path: str, mode: int = 0o644) -> None:
        sftp = self.client.open_sftp()
        try:
            parent = str(Path(remote_path).parent)
            self._mkdirs(sftp, parent)
            with sftp.file(remote_path, "wb") as fh:
                fh.write(data)
            sftp.chmod(remote_path, mode)
        finally:
            sftp.close()
        if self.target.become:
            self.run_root(f"chown {self.target.become}:{self.target.become} {remote_path!r}")

    def _mkdirs(self, sftp: paramiko.SFTPClient, path: str) -> None:
        parts = Path(path).parts
        cur = Path(parts[0]) if parts else Path("/")
        if parts and parts[0] == "/":
            accumulated = Path("/")
            rest = parts[1:]
        else:
            accumulated = Path(".")
            rest = parts
        for part in rest:
            accumulated = accumulated / part
            try:
                sftp.stat(str(accumulated))
            except FileNotFoundError:
                sftp.mkdir(str(accumulated))


def probe(target: SSHTarget) -> dict[str, str]:
    with Remote(target) as remote:
        code, out, err = remote.run("whoami; python3 --version; git --version; command -v codex || true")
        if code != 0:
            raise RuntimeError(err or out or f"probe failed ({code})")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return {
            "user": lines[0] if lines else "",
            "detail": out.strip(),
        }


def run_on_all(targets: Iterable[SSHTarget], command: str, timeout: int = 60) -> dict[str, tuple[int, str, str]]:
    results = {}
    for target in targets:
        with Remote(target) as remote:
            results[target.id] = remote.run(command, timeout=timeout)
    return results
