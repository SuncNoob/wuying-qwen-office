"""Git-backed protocol store. Runtime on ACs uses this with stdlib only."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from cowork.protocol import (
    COWORK_DIR,
    AgentCard,
    Claim,
    Task,
    dump_json,
    load_json,
    now,
)


class GitError(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.cowork = root / COWORK_DIR

    def ensure_layout(self) -> None:
        for name in ("agents", "tasks", "claims", "inbox", "results", "queue"):
            (self.cowork / name).mkdir(parents=True, exist_ok=True)

    def project_path(self) -> Path:
        return self.cowork / "project.json"

    def load_project(self) -> dict[str, Any]:
        path = self.project_path()
        if not path.exists():
            return {}
        return load_json(path)

    def save_project(self, data: dict[str, Any]) -> None:
        dump_json(self.project_path(), data)

    def agent_path(self, agent_id: str) -> Path:
        return self.cowork / "agents" / f"{agent_id}.json"

    def task_path(self, task_id: str) -> Path:
        return self.cowork / "tasks" / f"{task_id}.json"

    def claim_path(self, task_id: str) -> Path:
        return self.cowork / "claims" / f"{task_id}.json"

    def result_dir(self, task_id: str) -> Path:
        path = self.cowork / "results" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def inbox_dir(self, agent_id: str) -> Path:
        path = self.cowork / "inbox" / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load_agent(self, agent_id: str) -> AgentCard | None:
        path = self.agent_path(agent_id)
        if not path.exists():
            return None
        return AgentCard.from_dict(load_json(path))

    def save_agent(self, card: AgentCard) -> None:
        dump_json(self.agent_path(card.id), card.to_dict())

    def load_task(self, task_id: str) -> Task | None:
        path = self.task_path(task_id)
        if not path.exists():
            return None
        return Task.from_dict(load_json(path))

    def save_task(self, task: Task) -> None:
        task.updated_at = now()
        dump_json(self.task_path(task.id), task.to_dict())

    def load_claim(self, task_id: str) -> Claim | None:
        path = self.claim_path(task_id)
        if not path.exists():
            return None
        return Claim.from_dict(load_json(path))

    def save_claim(self, claim: Claim) -> None:
        dump_json(self.claim_path(claim.task_id), claim.to_dict())

    def clear_claim(self, task_id: str) -> None:
        path = self.claim_path(task_id)
        if path.exists():
            path.unlink()

    def all_tasks(self) -> dict[str, Task]:
        tasks: dict[str, Task] = {}
        folder = self.cowork / "tasks"
        if not folder.exists():
            return tasks
        for path in sorted(folder.glob("TASK-*.json")):
            try:
                task = Task.from_dict(load_json(path))
            except (ValueError, KeyError, OSError):
                continue
            tasks[task.id] = task
        return tasks

    def all_agents(self) -> dict[str, AgentCard]:
        agents: dict[str, AgentCard] = {}
        folder = self.cowork / "agents"
        if not folder.exists():
            return agents
        for path in sorted(folder.glob("*.json")):
            card = AgentCard.from_dict(load_json(path))
            agents[card.id] = card
        return agents

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            raise GitError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result

    def pull(self) -> None:
        if not self.has_upstream():
            return
        self.git("pull", "--rebase", "--autostash")

    def commit_all(self, message: str) -> bool:
        self.git("add", "-A")
        staged = self.git("diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            return False
        self.git("commit", "-m", message)
        return True

    def has_upstream(self) -> bool:
        result = self.git("rev-parse", "--abbrev-ref", "@{u}", check=False)
        return result.returncode == 0

    def push(self) -> None:
        if not self.has_upstream():
            return
        self.git("push")

    def sync_push(self, message: str, retries: int = 3) -> None:
        """Commit local protocol changes and push, rebasing on conflict."""
        if not self.commit_all(message):
            return
        if not self.has_upstream():
            return
        last_error = None
        for _ in range(retries):
            try:
                self.push()
                return
            except GitError as exc:
                last_error = exc
                pull = self.git("pull", "--rebase", "--autostash", check=False)
                if pull.returncode != 0:
                    self.git("rebase", "--abort", check=False)
                    raise GitError(pull.stderr.strip() or str(exc)) from exc
        if last_error:
            raise last_error
