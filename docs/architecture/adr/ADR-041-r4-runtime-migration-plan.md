# ADR-041: R4 Runtime Migration Plan

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-033 (R3 Plan), ADR-040 (R3 Final Convergence), ADR-009 (Delta Core Architecture), ADR-025 (R1 Final Convergence) |

## Context

R3 (Execution Lifecycle) is complete (ADR-040). All execution-lifecycle
decision authority — tool disposition, cancellation/timeout lifecycle state,
retry policy classification — is Rust-authoritative. Python retains
capability-plane responsibilities: tool execution, signal transport, deadline
mechanism, backoff math, message-history parsing.

R4 (Runtime) migrates the remaining runtime control authority. Per
`docs/governance/rust-core-migration.md` the R4 domains are:

- Task execution
- Workflow lifecycle
- Scheduler
- Automation Runtime
- Resume orchestration

Completion criterion: "R4 完成后，普通任务不得继续依赖 Legacy Python Runtime
作为主控。"

## Audit Findings

### Rust side today

`delta_core` is a synchronous request/response DB + decision service. It has
**no scheduler, no tick loop, no async runtime, no background threads**.
`main()` is a blocking `for line in stdin` loop. Protocol version 12. The
binary owns: run-event ledger, side-effect state machine, task identity
store, checkpoints, artifacts, sources/citations, validation, policy,
approval audit, tool-lifecycle disposition, retry classification. None of
these execute tasks or orchestrate runs.

### Python side today (the R4 surface)

| Domain | Current Python authority | Persisted where | Authority gap |
|---|---|---|---|
| Run lifecycle transitions | `runtime.py:_track` (lines 307–325) emits `run.started` / `run.resumed` / `run.completed` / `run.failed` — Python decides, Rust stores via `ledger.append` with **no state-machine validation** | `run-events.db` (Rust ledger) | Rust does not validate that a transition is legal (e.g. cannot `run.completed` an already-`run.interrupted` run) |
| Automation run status | `manager_automations.py:_run_scheduled_task` (lines 157–196) assigns `ok` / `error` / `skipped` / `validation_failed`; advances `run_count` / `last_run` / `last_status`; checks `max_runs` exhaustion — Python decides, Rust stores via `task.add_run` + `task.save` | `automation.db` (Rust taskstore) | Rust does not validate run status or enforce `max_runs` exhaustion atomically |
| Scheduler tick / catch-up / overlap | `core/automation/scheduler.py` — 30 s tick, run-once catch-up, in-memory `_running_ids` overlap guard | in-memory only | No persisted state — pure runtime mechanics |
| Next-fire computation | `store.py:compute_next_run` — croniter + ISO parse + DST-aware tz | `next_run` column in `automation.db` (Rust stores Python-computed value) | Pure function (given schedule spec + now → timestamp); no authority value beyond computation |
| Turn loop (continue / steer / end / compact) | `engine.py:_loop` — decides max-iterations exit, auto-compaction, transient retry, stream-cancel | not persisted (transcript only) | Reasoning capability — stays Python |
| Transcript persistence | `conversations.py` — Python sqlite3 + JSONL direct write | `core.db` + `.jsonl` files | Conversation history — reasoning ecosystem, not control-plane state |
| Session busy-set | `manager_sessions.py` — in-memory `_running_sessions` | in-memory only | Ephemeral concurrency control — runtime mechanics |
| Resume orchestration | `run.py:build_app` cold-start recovery; `manager_inbox.py:_durable_resume` | delegates to Rust | **Already Rust-authoritative** — `ledger.recover_stale`, `idem.sweep_stale`, `checkpoint.*`, `toollifecycle.plan` |
| Validation gate | `manager_automations.py:_validate_run` → `core.validation.run_validation` | `run-events.db` (Rust) | **Already Rust-authoritative** (ADR-028 hard-cut) — Python only collects artifacts + constructs criteria |

### No workflow engine exists

There is no graph/DAG/step-advance workflow engine in the codebase. "Workflow
lifecycle" as an R4 domain maps to the automation task/run lifecycle, which
is covered by the Automation Runtime domain below.

## Key Distinction

Same principle as R3 (ADR-033):

> **Pure functions and runtime mechanics stay Python; decision authority
> (state transitions with persisted consequences) goes to Rust.**

- **Backoff math** (ADR-033 audit): pure function → stays Python.
- **`compute_next_run`**: pure function (schedule spec + now → timestamp) →
  stays Python, same as backoff.
- **Scheduler tick / catch-up / overlap**: runtime mechanics, no persisted
  state → stays Python.
- **Turn loop**: reasoning capability (decides continue/steer/end based on
  LLM output) → stays Python.
- **Run lifecycle transition** (started → completed/failed/interrupted):
  persisted state-machine decision with crash-recovery consequences →
  **migrate to Rust**.
- **Automation run completion** (status assignment + stats advancement +
  max_runs exhaustion): persisted lifecycle decision → **migrate to Rust**.

## Phases

### Phase 1 — Run Lifecycle Transition Authority (ADR-042)

New Rust command `run.transition` that validates and appends run lifecycle
events. The ledger remains the storage layer; `run.transition` is the
decision layer that enforces the state machine.

State machine:

```
idle ──run.started──▶ running ──run.completed──▶ completed
                     running ──run.failed──────▶ failed
                     running ──run.interrupted──▶ interrupted
                     running ──run.resumed──▶ running (same run_id)
                     terminal ──any──▶ rejected (illegal transition)
```

Rules:
- `run.started` legal only if no prior terminal event for this run_id.
- `run.resumed` legal only if run is in `running` state (has `run.started`,
  no terminal).
- `run.completed` / `run.failed` legal only if run is `running` (not
  terminal).
- `run.interrupted` legal only if run is open (not terminal) — already used
  by `ledger.recover_stale`; becomes an internal call to the same validator.
- Terminal states are final; any transition from terminal is rejected.

Python `runtime.py:_track` becomes a thin caller: instead of
`self._ledger.append(run_id, "run.completed", ...)`, it calls
`self._ledger.transition(run_id, "completed", ...)`. Rust validates and
appends. Python no longer calls `ledger.append` directly for `run.*` events.

`ledger.recover_stale` internally calls the same transition validator.

Protocol 12 → 13.

### Phase 2 — Automation Run Completion Authority (ADR-043)

New Rust command `task.complete_run` that validates and atomically:

1. Assigns run status (`ok` / `error` / `skipped` / `validation_failed`).
2. Updates task stats (`run_count`, `last_run`, `last_status`).
3. Checks `max_runs` exhaustion — if `run_count >= max_runs`, atomically sets
   `enabled = 0` and `next_run = NULL`.

This makes the status assignment + exhaustion check atomic (no race where
two concurrent runs both pass the `max_runs` check before either increments).

Python `manager_automations.py` becomes a thin caller: instead of mutating
`TaskRun` fields and calling `task.add_run` + `task.save` separately, it
calls `task.complete_run` with the run outcome. Rust performs the atomic
update.

Protocol 13 → 14.

### Phase 3 — Scheduler Audit (ADR-044)

Audit-type ADR, no code change.

- `compute_next_run()`: pure function (croniter + ISO parse + DST-aware tz)
  → stays Python. Same classification as `backoff_delay()` (ADR-033 audit).
  The result is persisted by Rust (`task.save` stores `next_run`), but the
  computation is pure math with no authority value.
- Tick cadence (30 s constant), run-once catch-up policy, in-memory overlap
  guard: all runtime mechanics with no persisted state → stay Python.
- `task.due` query: already Rust.
- `max_runs` exhaustion: migrated in Phase 2.

No authority to migrate.

### Phase 4 — Resume Orchestration Audit (ADR-045)

Audit-type ADR, no code change.

Resume decision authority is already Rust-authoritative:

- `ledger.recover_stale` — decides open → `run.interrupted` (Rust).
- `idem.sweep_stale` — decides Planned/Executing → Uncertain (Rust).
- `checkpoint.register` / `checkpoint.validate` — Rust (ADR-029).
- `toollifecycle.plan` — decides execute/replay/uncertain (Rust, ADR-035).
- `toollifecycle.cancel` — decides Executing → Uncertain / Planned → Failed
  (Rust, ADR-037/038).

Remaining Python parts are pure orchestration glue (when to call
`recover_stale`, how to rebuild the engine, `unanswered_trailing_tool_calls`
message parsing) — runtime mechanics with no authority value, same as
ADR-036 concluded.

No authority to migrate.

### Phase 5 — R4 Final Convergence (ADR-046)

Document the final R4 authority matrix, protocol version history, and mark
R4 complete.

## Migration Order

```
Phase 1 (ADR-042)  →  Phase 2 (ADR-043)  →  Phase 3 (ADR-044, audit)
                                              ↓
                                            Phase 4 (ADR-045, audit)
                                              ↓
                                            Phase 5 (ADR-046, convergence)
```

Phase 1 before Phase 2: run lifecycle transitions are the foundation; automation
run completion builds on top of the same run state machine.

## Authority After R4

| Domain | Rust Authority | Python Capability |
|---|---|---|
| Run lifecycle transitions | `run.transition` — validates + appends | `runtime.py:_track` thin caller |
| Automation run completion | `task.complete_run` — status + stats + max_runs atomic | `manager_automations.py` thin caller |
| Scheduler tick / catch-up / overlap | N/A (runtime mechanics) | `scheduler.py` — 30 s tick, overlap guard |
| Next-fire computation | N/A (pure function) | `compute_next_run` — croniter + tz |
| Turn loop | N/A (reasoning capability) | `engine.py:_loop` — continue/steer/end/compact |
| Transcript | N/A (conversation history) | `conversations.py` — sqlite3 + JSONL |
| Resume orchestration | `ledger.recover_stale` + `idem.sweep_stale` + `checkpoint.*` + `toollifecycle.plan` | Orchestration glue (when to call, engine rebuild) |
| Validation gate | `validation.run` / `validation.eval` (ADR-028) | Artifact collection + criteria construction |

## Risk Assessment

- **Phase 1** is the highest-risk change: it touches the run lifecycle hot
  path (`_track` runs on every turn). Mitigation: the transition validator is
  a thin layer over the existing `ledger.append`; if it rejects a transition
  that Python previously emitted freely, that's a regression. Must verify
  all existing transition patterns are legal under the new state machine.
- **Phase 2** is medium risk: touches automation run finalization. The
  atomicity gain (no race on `max_runs`) is a correctness improvement.
- **Phases 3–4** are doc-only audits (zero code risk).

## Rollback

Each phase is an independent PR with independent rollback (Git revert). No
data migration required — the schema is unchanged (Phase 1 reuses
`run_events.db`; Phase 2 reuses `automation.db`).
