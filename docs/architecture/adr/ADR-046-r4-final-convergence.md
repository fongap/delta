# ADR-046: R4 Final Convergence

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-040 (R3 Final Convergence), ADR-041 (R4 Plan), ADR-042 (Run Lifecycle), ADR-043 (Automation Completion), ADR-044 (Scheduler Audit), ADR-045 (Resume Audit) |

## Context

R4 (Runtime) was planned in ADR-041 as a four-phase migration of runtime control authority:

- **Phase 1** (ADR-042): Run Lifecycle Transition Authority — `run.transition` command enforcing run state machine
- **Phase 2** (ADR-043): Automation Run Completion Authority — `task.complete_run` atomic run status + task stats + max_runs
- **Phase 3** (ADR-044): Scheduler Audit — no migration needed (pure functions + runtime mechanics)
- **Phase 4** (ADR-045): Resume Orchestration Audit — no migration needed (already Rust-authoritative)

This ADR records the final state after all phases complete.

## R4 Domain Authority Matrix (Final)

| Domain | Rust Authority | Python Capability | ADR |
|---|---|---|---|
| Run lifecycle transitions (started/resumed/completed/failed/interrupted) | `run.transition` — validates state machine | `_track` thin caller | ADR-042 |
| Automation run completion (status + stats + max_runs) | `task.complete_run` — atomic status + run_count + exhaustion | `_run_scheduled_task` thin caller | ADR-043 |
| Due task query | `task.due` | `Scheduler._tick` calls Rust | R1/R2 |
| Next-fire computation | N/A (pure fn) | `compute_next_run` (croniter + DST) | ADR-044 (audit: stays Python) |
| Scheduler tick / catch-up / overlap | N/A (mechanics) | `Scheduler._loop` / `_running_ids` | ADR-044 (audit: stays Python) |
| Trigger dispatch | N/A (unwired) | `TriggerRegistry` (not production) | ADR-044 (audit: stays Python) |
| Cold-start recovery | `recover_stale` + `sweep_stale` + `checkpoint.*` | Orchestration glue (`run.py`, `manager_inbox.py`) | ADR-045 (audit: already Rust) |
| Resume identity & `run.resumed` | `run.transition` | `_last_run_id` reuse | ADR-042 |
| Tool call disposition (execute/replay/uncertain) | `toollifecycle.plan` | `authorize_and_execute` thin caller | ADR-035 |
| Tool call cancellation/timeout | `toollifecycle.cancel` (reason) | `_interrupt_tool` thin caller | ADR-037/038 |
| Pending tool call reconstruction | N/A (pure parse) | `unanswered_trailing_tool_calls()` | ADR-036 (audit: stays Python) |
| Resume orchestration glue | N/A (glue) | `manager_inbox.py`, `manager_gateway.py` | ADR-045 (audit: stays Python) |
| Self-wake resume | Same as Inbox resume | `resume_due_wakes` | ADR-045 (audit: same path) |

## Protocol Version History

| Bump | Command Added | ADR |
|---|---|---|
| 9 → 10 | `toollifecycle.cancel` | ADR-037 revised |
| 10 → 11 | `toollifecycle.cancel` `reason` field | ADR-038 |
| 11 → 12 | `retry.classify` | ADR-039 |
| 12 → 13 | `run.transition` | ADR-042 |
| 13 → 14 | `task.complete_run` | ADR-043 |

**Final protocol version: 14.**

## Completion Criteria Met

Per `docs/governance/rust-core-migration.md:178-188`, R4 completion criterion:

> **R4 完成后，普通任务不得继续依赖 Legacy Python Runtime 作为主控。**

This is satisfied:
- **Run lifecycle decisions** (started/resumed/completed/failed/interrupted) — Rust validates state machine via `run.transition`
- **Automation run completion** (status + stats + max_runs) — Rust atomic via `task.complete_run`
- **Scheduler due query** — Rust `task.due` (since R1)
- **Resume orchestration** — All decision points in Rust (`recover_stale`, `sweep_stale`, `run.transition`, `toollifecycle.plan/cancel`)
- **Python retains**: Pure functions (`compute_next_run`, `backoff_delay`), runtime mechanics (tick loop, overlap guard), orchestration glue (when to resume, engine rebuild), pure parsing (`unanswered_trailing_tool_calls`)

## Decision

**R4 is complete.** All runtime control authority is Rust-authoritative. Python retains capability-plane responsibilities only.

## Consequences

- Protocol version finalized at 14
- No further R4 migrations needed
- Next phase: R5 (Provider Core) per migration plan
- Authority Matrix in governance doc updated