# ADR-045: R4 Resume Orchestration Audit

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-029 (Checkpoint Hard-Cut), ADR-035 (Tool Lifecycle Hard-Cut), ADR-036 (Resume Decision Audit), ADR-037/038 (Cancellation/Timeout), ADR-041 (R4 Plan), ADR-042/043/044 (R4 Phases 1-3) |

## Context

R4 Phase 4 audits the **Resume Orchestration** domain (part of R4 "Resume orchestration" migration target). The question: does any resume decision authority remain in Python that should migrate to Rust?

Per the R4 Plan (ADR-041), the resume orchestration domain includes the cold-start recovery sequence and the durable resume flow that rebuilds engine state after interruptions (approval, question, directory, plan, crash).

## Audit Findings

### 1. Cold-Start Recovery — Already Rust-Authoritative

**Sequence** (`services/server/run.py:113-163`):
1. `run_ledger.recover_stale()` → Rust `LedgerWriter::recover_stale` (ADR-025)
   - Scans for runs without terminal event
   - Appends synthetic `run.interrupted {reason: "crashed"}`
   - **Authority**: Rust decides which runs are "interrupted"
2. `idem_log.sweep_stale(interrupted_ids)` → Rust `IdempotencyWriter::sweep_stale` (ADR-029)
   - Finds Planned/Executing side effects in interrupted runs
   - Transitions them to `Uncertain`
   - **Authority**: Rust decides Planned/Executing → Uncertain
3. `recovery_store.latest()` → Rust `checkpoint.list` (ADR-029)
   - Advisory only: logs paused sessions count

**Verdict**: Core recovery decisions already Rust-authoritative.

### 2. Durable Resume Flow — Orchestration Glue Only

**Entry points** (`services/server/manager_inbox.py:183-210`, `manager_gateway.py:266-280`):
- `_durable_resume(item)`: triggered by Inbox resolution or self-wake
- Calls `get_engine(session_id)` → builds fresh `TurnEngineAdapter`
- Calls `runtime.resume()` → engine rebuilds from messages
- Calls `self.save()` + `recovery_store.clear()` on completion

**Python's role**: Pure orchestration — decides *when* to call resume, rebuilds engine, manages session lifecycle. The actual state-machine decisions happen in Rust:
- `TurnEngineAdapter.resume()` → `_track(kind="resume")` → emits `run.resumed` via `run.transition` (ADR-042)
- `TurnEngine.resume()` calls `unanswered_trailing_tool_calls()` (pure Python parsing, ADR-036: no authority)
- `ToolLifecycleOrchestrator.authorize_and_execute()` → calls `toollifecycle.plan` for each pending tool call → Rust decides execute/replay/uncertain (ADR-035)

**Verdict**: Python is pure orchestration glue. No decision authority to migrate.

### 3. Pending Tool Call Reconstruction — Pure Parsing (ADR-036)

**Function**: `ToolLifecycleOrchestrator.unanswered_trailing_tool_calls()` (`core/tool_lifecycle.py:251-278`)
- Parses `ctx.messages` (in-memory conversation history)
- Finds trailing assistant tool calls without matching tool results
- **Authority**: Pure in-memory parsing, no persisted state, no crash-recovery consequences
- **ADR-036**: Already audited — "no authority value, not migrating"

**Verdict**: Already covered by ADR-036.

### 4. Crash Recovery Identity — Preserved by Rust

**Key invariant**: `TurnEngineAdapter._last_run_id` preserves run identity across resume
- First run: mints new `run_id` (or uses automation's `run_id`)
- `self._last_run_id` updated after first `_track`
- Resume: reuses `_last_run_id` so idempotency log sees replay
- `run.transition` emits `run.resumed` (not `run.started`) for same `run_id`
- **Rust enforces**: `run.resumed` legal from `interrupted` (ADR-042 state machine)

**Verdict**: Identity preservation already enforced by Rust state machine.

### 5. Self-Wake Resume — Same Path

**Flow**: `manager_gateway.py:resume_due_wakes` → `deliver_to_session` → `runtime.resume()`
- Same `runtime.resume()` path as Inbox resume
- Same Rust decisions apply

**Verdict**: No additional authority.

## Summary: Resume Orchestration Authority Matrix

| Sub-domain | Authority | Location | Migration |
|---|---|---|---|
| Cold-start: run interrupted | **Rust** | `ledger.recover_stale` | ✅ Done (ADR-025) |
| Cold-start: side effects → Uncertain | **Rust** | `idem.sweep_stale` | ✅ Done (ADR-029) |
| Cold-start: checkpoint advisory | **Rust** | `checkpoint.list` | ✅ Done (ADR-029) |
| Resume: run identity + `run.resumed` | **Rust** | `run.transition` | ✅ Done (ADR-042) |
| Resume: tool call execute/replay/uncertain | **Rust** | `toollifecycle.plan` | ✅ Done (ADR-035) |
| Resume: cancellation/timeout of pending calls | **Rust** | `toollifecycle.cancel` | ✅ Done (ADR-037/038) |
| Resume: pending tool call reconstruction | **Python** (pure parse) | `unanswered_trailing_tool_calls` | ❌ No migration (ADR-036) |
| Resume: orchestration glue (when to call, engine rebuild) | **Python** (glue) | `manager_inbox.py`, `manager_gateway.py` | ❌ No authority |
| Self-wake resume | Same as Inbox resume | `manager_gateway.py:resume_due_wakes` | ❌ Same path |

## Summary

**All resume decision authority is already Rust-authoritative.** The remaining Python code is:
1. Pure orchestration glue (when to call resume, engine rebuild, session management)
2. Pure in-memory parsing (`unanswered_trailing_tool_calls`) — already audited in ADR-036

**No resume decision authority remains to migrate to Rust.**

## Decision

**Phase 4 is an audit closure. No code changes. No PR beyond this ADR.**

## Consequences

- Python retains resume orchestration glue (when to resume, engine rebuild)
- Rust retains all resume decision authority (state machine, idempotency, cancellation)
- No protocol version bump needed
- No new tests required beyond existing resume/recovery tests