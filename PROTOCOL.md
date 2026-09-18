# Cowork Protocol 1.0

Multiple 无影 Agentic Computers collaborate through **one Git repository**.
They never talk peer-to-peer. The repo is the bus, the board, and the audit log.

## State machine

```
open → claimed → in_progress → review → done
                 ↘ failed
claimed (lease expired, no heartbeat) → open
```

## Files (all under `.cowork/`)

| Path | Writer | Purpose |
|---|---|---|
| `project.json` | human / CLI | scenario, members, protocol version |
| `agents/<id>.json` | that agent only | card + heartbeat |
| `tasks/<id>.json` | human, planner, owner | task record |
| `claims/<id>.json` | claimant | atomic lease |
| `inbox/<agent-id>/` | any agent / human | directed messages |
| `results/<task-id>/` | owner | deliverables |

## Atomic claim

1. `git pull --rebase`
2. If `claims/<id>.json` exists and lease is still valid, abort
3. Write claim `{task_id, agent_id, claimed_at, lease_until}`
4. `git add && git commit && git push`
5. If push rejected, pull --rebase; if another claim won, abort

Never `--force`. Heartbeat files are per-agent, so they do not collide.

## Capabilities

A task is claimable only when the agent's `capabilities` contain the task `type`.

| Scenario | Types | Typical role |
|---|---|---|
| coding | `plan`, `implement`, `review` | Planner / Builder / Reviewer |
| research | same types, `mode=research` | Codex writes a research note; reviewer checks 命题/已知结果/尝试/缺口 |
| crawler | `enqueue`, `fetch`, `extract` | Dispatcher / Fetcher / Analyst |

## Secrets

GitHub tokens, SSH keys, and model gateway keys **must not** be committed.
They live in `cowork.local.json` (laptop) or AC environment variables.
