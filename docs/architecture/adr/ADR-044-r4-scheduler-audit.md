# ADR-044: R4 Scheduler Audit

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-041 (R4 Plan), ADR-033 (R3 Execution Lifecycle Plan), ADR-042 (Run Lifecycle Transition), ADR-043 (Automation Run Completion) |

## Context

R4 Phase 3 audits the **Scheduler** domain (part of the R4 "Scheduler" migration target per `docs/governance/rust-core-migration.md`). The scheduler is responsible for:
- Deciding **when** scheduled tasks fire (tick cadence, catch-up policy)
- Deciding **which** tasks are due (filtering enabled tasks with `next_run <= now`)
- Computing **next fire time** after each run (`compute_next_run`)
- Preventing overlapping runs (in-memory overlap guard)

The Rust side (`delta_core`) already owns the **due task query** (`task.due` command, implemented in `taskstore.rs`). The question is whether any remaining scheduler authority should migrate to Rust.

## Audit Findings

### 1. Due Task Query — Already Rust-Authoritative
- **Rust**: `task.due` command (`taskstore.rs:183-199`) implements `WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?`
- **Python**: `TaskStore.due()` (`store.py:155-159`) delegates to Rust
- **Verdict**: Authority already in Rust. No migration needed.

### 2. Next-Fire Computation (`compute_next_run`) — Pure Function
- **Location**: `core/automation/store.py:24-64` (`compute_next_run` + `_tz`)
- **Logic**: 
  - `cron` schedules: uses `croniter` library for cron expression parsing + next fire time
  - `once` schedules: ISO 8601 parse, returns that timestamp once
  - DST-aware timezone handling via `_tz` helper
- **Called from**: `TaskStore.save()` before `task.save` write (line 125)
- **Authority classification**: **Pure function** — given a schedule spec + `now` timestamp → returns next fire timestamp. No side effects, no state, no crash-recovery consequences.
- **Precedent**: ADR-033 audit classified `backoff_delay()` (pure math) as non-migratable. `compute_next_run` is the same category.
- **Verdict**: **Stays Python**. No authority value to migrate.

### 3. Tick Loop & Catch-Up Policy — Runtime Mechanics
- **Location**: `core/automation/scheduler.py`
- **Logic**:
  - 30-second fixed tick interval (`tick_seconds = 30.0`, line 29)
  - First tick: `trigger="catchup"` — fires all overdue tasks once on startup
  - Subsequent ticks: `trigger="schedule"` — fires tasks with `next_run <= now`
  - In-memory overlap guard: `_running_ids` set prevents concurrent runs of same task
- **Persistence**: None. Overlap guard is in-memory only (survives process restart via catch-up tick)
- **Authority classification**: **Runtime mechanics** — no persistent state decisions, no crash-recovery consequences beyond what catch-up already handles.
- **Verdict**: **Stays Python**. No authority value to migrate.

### 4. Overlap Guard — In-Memory Concurrency Control
- **Mechanism**: `_running_ids: set[str]` in `Scheduler` (line 38)
- **Behavior**: Claims task_id on tick, releases in `finally` block
- **Crash behavior**: If process dies mid-run, the set is lost; catch-up tick on restart will re-fire the task (which is correct — the run was interrupted, not completed)
- **Authority classification**: Ephemeral concurrency control — not persisted, no long-term authority.
- **Verdict**: **Stays Python**.

### 5. Trigger Dispatch (`TriggerRegistry`) — Unwired
- **Location**: `core/automation/triggers.py`
- **Status**: Not wired into production runtime (only tested in `tests/test_conditional_triggers.py`)
- **Logic**: Event-driven "when" (file watch, inbox, manual) with cooldown + dedup
- **Verdict**: Not in scope for R4 (not production-wired). If wired later, re-audit.

### 6. Max Runs Exhaustion — Migrated in Phase 2 (ADR-043)
- **Before**: Python checked `run_count >= max_runs` in `Scheduler._execute` (line 127-134), then set `enabled=false` + `next_run=None` + `task.save()`
- **After**: Rust `task.complete_run` atomically increments `run_count`, checks `max_runs`, and disables task if exhausted — in same transaction as run completion.
- **Verdict**: Authority already migrated in Phase 2 (ADR-043).

## Summary: Scheduler Authority Matrix

| Sub-domain | Authority | Location | Migration |
|---|---|---|---|
| Due task query | **Rust** | `taskstore.rs` `due_tasks` | ✅ Done (R1/R2) |
| Next-fire computation | **Python** (pure fn) | `store.py` `compute_next_run` | ❌ No migration (pure fn) |
| Tick cadence / catch-up | **Python** (mechanics) | `scheduler.py` `_loop` | ❌ No migration (mechanics) |
| Overlap guard | **Python** (ephemeral) | `scheduler.py` `_running_ids` | ❌ No migration (ephemeral) |
| Trigger dispatch | **Python** (unwired) | `triggers.py` `TriggerRegistry` | ❌ Not in scope |
| Max runs exhaustion | **Rust** (atomic) | `taskstore.rs` `complete_run` | ✅ Done (Phase 2) |

## Decision

**No scheduler authority remains to migrate to Rust.** The only decision-authority sub-domains (due query, max-runs exhaustion) are already Rust-authoritative. The remaining Python code consists of:
- Pure functions (`compute_next_run`) — same category as `backoff_delay` (ADR-033)
- Runtime mechanics (tick loop, catch-up, overlap guard) — ephemeral, no persisted authority
- Unwired code (`TriggerRegistry`) — not in production

**Phase 3 is an audit closure. No code changes. No PR beyond this ADR.**

## Consequences

- Python retains scheduler tick loop, catch-up logic, and next-fire computation
- Rust retains due-task query and (via Phase 2) max-runs exhaustion
- No protocol version bump needed
- No new tests required beyond existing scheduler tests