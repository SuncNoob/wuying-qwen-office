"""Agent loop: pull, heartbeat, claim, execute, push."""

from __future__ import annotations

import argparse
import traceback
from typing import Callable

from cowork.codex_run import CODEX_LEASE_SECONDS, uses_codex_session
from cowork.browser import DEFAULT_MAX_IMAGES, brand_from_url
from cowork.executors import run_enqueue, run_extract, run_fetch, run_implement, run_review
from cowork.protocol import (
    DEFAULT_LEASE_SECONDS,
    AgentCard,
    Task,
    claim_is_taken,
    make_claim,
    next_task_id,
    now,
)
from cowork.store import GitError, Store


def heartbeat(store: Store, card: AgentCard) -> AgentCard:
    card.heartbeat_at = now()
    store.save_agent(card)
    return card


def dependencies_ready(store: Store, task: Task) -> bool:
    return task.dependencies_done(store.all_tasks())


def find_claimable(store: Store, card: AgentCard) -> Task | None:
    tasks = store.all_tasks()
    for task in tasks.values():
        if task.status != "open":
            continue
        if not card.can_handle(task.type):
            continue
        if not task.dependencies_done(tasks):
            continue
        existing = store.load_claim(task.id)
        if claim_is_taken(existing, card.id):
            continue
        return task
    return None


def try_claim(store: Store, card: AgentCard, task: Task, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    existing = store.load_claim(task.id)
    if claim_is_taken(existing, card.id):
        return False
    claim = make_claim(task.id, card.id, lease_seconds=lease_seconds)
    store.save_claim(claim)
    task.status = "claimed"
    task.owner = card.id
    store.save_task(task)
    try:
        store.sync_push(f"cowork({card.id}): claim {task.id}")
    except GitError:
        store.pull()
        winner = store.load_claim(task.id)
        if winner and winner.agent_id != card.id and winner.active():
            return False
        try:
            store.sync_push(f"cowork({card.id}): claim {task.id}")
        except GitError:
            return False
    return True


def spawn_followups(store: Store, task: Task, result: dict) -> list[Task]:
    """Planner/dispatcher create the next tasks in the scenario pipeline."""
    created: list[Task] = []
    ts = now()
    existing = list(store.all_tasks())
    if task.type == "plan":
        impl_id = next_task_id(existing)
        existing.append(impl_id)
        impl = Task(
            id=impl_id,
            scenario="research" if task.scenario == "research" or task.mode == "research" else "coding",
            type="implement",
            title=f"Implement: {task.title}",
            body=task.body,
            path=task.path,
            content=task.content,
            need=list(task.need or []),
            mode="research" if task.mode == "research" or task.scenario == "research" else task.mode,
            status="open",
            created_at=ts,
            updated_at=ts,
        )
        review_id = next_task_id(existing)
        review = Task(
            id=review_id,
            scenario=impl.scenario,
            type="review",
            title=f"Review: {task.title}",
            depends_on=[impl_id],
            mode=impl.mode,
            status="open",
            created_at=ts,
            updated_at=ts,
        )
        store.save_task(impl)
        store.save_task(review)
        created.extend([impl, review])
    elif task.type == "enqueue":
        urls = list(result.get("urls") or task.seed_urls)
        fetch_ids: list[str] = []
        mode = task.mode or "http"
        for url in urls:
            fetch_id = next_task_id(existing)
            existing.append(fetch_id)
            fetch_ids.append(fetch_id)
            from cowork.browser import brand_from_url as _brand_of
            brand = task.brand or _brand_of(url)
            fetch = Task(
                id=fetch_id,
                scenario="crawler",
                type="fetch",
                title=f"Fetch {brand or url}",
                seed_urls=[url],
                allow_hosts=task.allow_hosts,
                depends_on=[task.id],
                mode=mode,
                brand=brand,
                max_images=task.max_images or DEFAULT_MAX_IMAGES,
                status="open",
                created_at=ts,
                updated_at=ts,
            )
            store.save_task(fetch)
            created.append(fetch)
        if fetch_ids:
            extract_id = next_task_id(existing)
            extract = Task(
                id=extract_id,
                scenario="crawler",
                type="extract",
                title=f"Catalog images from {task.id}",
                depends_on=fetch_ids,
                allow_hosts=task.allow_hosts,
                status="open",
                created_at=ts,
                updated_at=ts,
            )
            store.save_task(extract)
            created.append(extract)
    return created


def execute(store: Store, card: AgentCard, task: Task) -> dict:
    task.status = "in_progress"
    store.save_task(task)
    store.sync_push(f"cowork({card.id}): start {task.id}")
    tasks = store.all_tasks()
    runners: dict[str, Callable[..., dict]] = {
        "plan": lambda: {"ok": True, "spawned": True},
        "implement": lambda: run_implement(store, task),
        "review": lambda: run_review(store, task, tasks),
        "enqueue": lambda: run_enqueue(store, task),
        "fetch": lambda: run_fetch(store, task),
        "extract": lambda: run_extract(store, task, tasks),
    }
    if task.type not in runners:
        raise ValueError(f"unsupported task type: {task.type}")
    result = runners[task.type]()
    spawn_followups(store, task, result)
    if task.type == "review":
        task.status = "done" if result.get("ok") else "failed"
    elif task.type == "extract":
        task.status = "done" if result.get("ok") else "failed"
    else:
        task.status = "done" if result.get("ok", True) else "failed"
    store.save_task(task)
    store.sync_push(f"cowork({card.id}): finish {task.id} {task.status}")
    return result


def tick(store: Store, card: AgentCard, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> str:
    store.ensure_layout()
    try:
        store.pull()
    except GitError:
        pass
    heartbeat(store, card)
    try:
        store.sync_push(f"cowork({card.id}): heartbeat")
    except GitError:
        try:
            store.pull()
            store.sync_push(f"cowork({card.id}): heartbeat")
        except GitError:
            pass

    owned = [
        t
        for t in store.all_tasks().values()
        if t.owner == card.id and t.status in {"claimed", "in_progress"}
    ]
    if owned:
        try:
            execute(store, card, owned[0])
            return f"executed {owned[0].id}"
        except Exception:
            owned[0].status = "failed"
            store.save_task(owned[0])
            store.result_dir(owned[0].id).joinpath("error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            store.sync_push(f"cowork({card.id}): fail {owned[0].id}")
            return f"failed {owned[0].id}"

    task = find_claimable(store, card)
    if task is None:
        return "idle"
    lease = CODEX_LEASE_SECONDS if uses_codex_session(task.mode) else lease_seconds
    if try_claim(store, card, task, lease_seconds=lease):
        return f"claimed {task.id}"
    return "claim-lost"


def wake_path(store: Store, card: AgentCard):
    return store.inbox_dir(card.id) / "wake"


def wait_interval(store: Store, card: AgentCard, interval: int) -> None:
    """Sleep up to `interval` seconds, but return immediately if a wake file appears.

    The laptop monitor touches `.cowork/inbox/<id>/wake` so idle agents pick up
    newly dispatched tasks without waiting for the next poll.
    """
    import time

    path = wake_path(store, card)
    deadline = time.time() + max(0, interval)
    while True:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
            return
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def loop(store: Store, card: AgentCard, interval: int, once: bool = False) -> None:
    while True:
        tick(store, card)
        if once:
            return
        wait_interval(store, card, interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coworkd")
    parser.add_argument("--root", default=".")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    store = Store(PathArg(args.root))
    card = store.load_agent(args.agent_id)
    if card is None:
        raise SystemExit(f"agent card not found: {args.agent_id}")
    loop(store, card, interval=args.interval, once=args.once)
    return 0


def PathArg(value: str):
    from pathlib import Path

    return Path(value).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
