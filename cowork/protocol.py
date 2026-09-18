"""Protocol models, matching rules, and claim/lease helpers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

PROTOCOL_VERSION = "1.0"
COWORK_DIR = ".cowork"
DEFAULT_LEASE_SECONDS = 300
TASK_ID_RE = re.compile(r"^TASK-\d{3,}$")

SCENARIO_TYPES = {
    "coding": ("plan", "implement", "review"),
    "research": ("plan", "implement", "review"),
    "crawler": ("enqueue", "fetch", "extract"),
}

ROLE_CAPABILITIES = {
    "planner": ["plan"],
    "builder": ["implement"],
    "reviewer": ["review"],
    "dispatcher": ["enqueue"],
    "fetcher": ["fetch"],
    "analyst": ["extract"],
}

DEFAULT_SCENARIO_ROLES = {
    "coding": ("planner", "builder", "reviewer"),
    "research": ("planner", "builder", "reviewer"),
    "crawler": ("dispatcher", "fetcher", "analyst"),
}


def now() -> int:
    return int(time.time())


def next_task_id(existing: Iterable[str]) -> str:
    nums = []
    for item in existing:
        m = re.match(r"TASK-(\d+)$", item)
        if m:
            nums.append(int(m.group(1)))
    return f"TASK-{max(nums, default=0) + 1:03d}"


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class AgentCard:
    id: str
    role: str
    capabilities: list[str]
    model: str = "Auto"
    host: str = ""
    heartbeat_at: int = 0
    protocol: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCard":
        return cls(
            id=str(data["id"]),
            role=str(data.get("role", "")),
            capabilities=list(data.get("capabilities") or []),
            model=str(data.get("model", "Auto")),
            host=str(data.get("host", "")),
            heartbeat_at=int(data.get("heartbeat_at") or 0),
            protocol=str(data.get("protocol", PROTOCOL_VERSION)),
        )

    def can_handle(self, task_type: str) -> bool:
        return task_type in self.capabilities


@dataclass
class Task:
    id: str
    scenario: str
    type: str
    title: str
    status: str = "open"
    body: str = ""
    need: list[str] = field(default_factory=list)
    owner: str = ""
    seed_urls: list[str] = field(default_factory=list)
    allow_hosts: list[str] = field(default_factory=list)
    path: str = ""
    content: str = ""
    depends_on: list[str] = field(default_factory=list)
    mode: str = "http"
    brand: str = ""
    max_images: int = 0
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            id=str(data["id"]),
            scenario=str(data.get("scenario", "")),
            type=str(data["type"]),
            title=str(data.get("title", data["id"])),
            status=str(data.get("status", "open")),
            body=str(data.get("body", "")),
            need=list(data.get("need") or []),
            owner=str(data.get("owner", "")),
            seed_urls=list(data.get("seed_urls") or []),
            allow_hosts=list(data.get("allow_hosts") or []),
            path=str(data.get("path", "")),
            content=str(data.get("content", "")),
            depends_on=list(data.get("depends_on") or []),
            mode=str(data.get("mode") or "http"),
            brand=str(data.get("brand") or ""),
            max_images=int(data.get("max_images") or 0),
            created_at=int(data.get("created_at") or 0),
            updated_at=int(data.get("updated_at") or 0),
        )

    def dependencies_done(self, tasks: dict[str, "Task"]) -> bool:
        return all(tasks.get(dep) and tasks[dep].status == "done" for dep in self.depends_on)


@dataclass
class Claim:
    task_id: str
    agent_id: str
    claimed_at: int
    lease_until: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        return cls(
            task_id=str(data["task_id"]),
            agent_id=str(data["agent_id"]),
            claimed_at=int(data["claimed_at"]),
            lease_until=int(data["lease_until"]),
        )

    def active(self, at: int | None = None) -> bool:
        return self.lease_until > (at if at is not None else now())


def make_claim(task_id: str, agent_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Claim:
    ts = now()
    return Claim(
        task_id=task_id,
        agent_id=agent_id,
        claimed_at=ts,
        lease_until=ts + lease_seconds,
    )


def claim_is_taken(existing: Claim | None, agent_id: str, at: int | None = None) -> bool:
    if existing is None:
        return False
    if not existing.active(at):
        return False
    return existing.agent_id != agent_id


def host_allowed(url: str, allow_hosts: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    allowed = {h.lower() for h in allow_hosts}
    return host in allowed or any(host.endswith("." + h) for h in allowed)


def capabilities_for_role(role: str) -> list[str]:
    if role not in ROLE_CAPABILITIES:
        raise ValueError(f"unknown role: {role}")
    return list(ROLE_CAPABILITIES[role])
