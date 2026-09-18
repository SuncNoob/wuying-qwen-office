"""Create a cowork project layout in a git repo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cowork.protocol import (
    PROTOCOL_VERSION,
    ROLE_CAPABILITIES,
    AgentCard,
    Task,
    now,
)
from cowork.store import Store


def init_project(
    root: Path,
    name: str,
    scenario: str,
    agents: list[dict[str, Any]],
    remote: str = "",
) -> Store:
    store = Store(root)
    store.ensure_layout()
    roles = {item["role"]: item["id"] for item in agents}
    project = {
        "protocol": PROTOCOL_VERSION,
        "name": name,
        "scenario": scenario,
        "remote": remote,
        "roles": roles,
        "created_at": now(),
    }
    store.save_project(project)
    for item in agents:
        role = item["role"]
        card = AgentCard(
            id=item["id"],
            role=role,
            capabilities=list(ROLE_CAPABILITIES[role]),
            model=item.get("model", "Auto"),
            host=str(item.get("port") or item.get("host") or ""),
            heartbeat_at=0,
        )
        store.save_agent(card)
        store.inbox_dir(card.id)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {name}\n\nCowork project ({scenario}). See PROTOCOL.md.\n",
            encoding="utf-8",
        )
    return store


def add_task(store: Store, task: Task) -> Task:
    if not task.created_at:
        task.created_at = now()
    store.save_task(task)
    return task
