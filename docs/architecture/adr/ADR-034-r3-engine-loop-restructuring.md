# ADR-034: R3 Engine Loop Restructuring — Tool Lifecycle Orchestrator Extraction

- Status: Accepted
- Date: 2026-09-10
- Scope: R3 Execution Lifecycle Phase 1 — extract tool-call lifecycle orchestration from TurnEngine into a dedicated module
- Decision Type: Refactoring / Migration-phase prerequisite
- Related: ADR-033 (R3 Plan), ADR-032 (R2 Final Convergence)

## Background

R3 Execution Lifecycle (ADR-033) identified that all remaining R3 domains (Tool Lifecycle, Resume, Cancellation, Timeout, Retry) depend on the engine execution loop in `core/engine.py`. The loop intertwines:
- **Async streaming** (`_astream`, TTFT, model turn production)
- **Tool-call orchestration** (`_handle_tool_calls`, `_execute_sync`, authorization, execution dispatch)
- **Durable resume** (`resume`, `_unanswered_trailing_tool_calls`)
- **Cancellation/Interrupt** (`_cancel`, `_interruptible`, executor hooks)

None of these can be hard-cut to Rust until the tool-call lifecycle is **separable** from the streaming loop. This ADR documents the extraction.

## Decision

Extract a **Tool Lifecycle Orchestrator** (`core/tool_lifecycle.py`) that encapsulates:

1. **Authorization phase** (from `_authorize` + `_handle_tool_calls`):
   - Gateway slice 1 (`classify`) → permission engine → gateway slices 2-4 (`evaluate_policy`)
   - Approval/plan/directory/ask_user interactive paths
   - Concurrent vs serial dispatch decision (`_parallel_safe`)

2. **Execution phase** (from `_execute_sync` + `_record_result`):
   - Idempotency state machine orchestration: `lookup` → `record_planned` → `mark_executing` → `commit`/`mark_failed`
   - Tool execution (`registry.execute`)
   - Result recording + audit + artifact registration

3. **Resume orchestration** (from `resume` + `_unanswered_trailing_tool_calls`):
   - Reconstruct unanswered trailing tool calls from message history
   - Re-run them through the orchestrator (idempotency lookup handles dedup)

4. **Cancel/Interrupt integration**:
   - Accept a `CancellationToken` (to replace Python `_cancel` Event later)
   - Surface uncertain/denied/error results on cancel

The `TurnEngine` (`core/engine.py`) becomes:
- **Streaming loop owner**: `_astream`, TTFT, model turn production
- **High-level loop**: iteration count, compaction triggers, steering injection
- **Thin wrapper** over the Tool Lifecycle Orchestrator: calls `orchestrator.authorize_and_execute(calls)` and `orchestrator.resume(pending_calls)`

## Non-Goals

This ADR does **not** hard-cut any domain to Rust. It is a **Python refactoring** that enables future hard-cuts. The orchestrator remains in Python initially; future ADRs (ADR-035+) will migrate it to Rust.

## Implementation

### New Module: `core/tool_lifecycle.py`

```python
class ToolLifecycleOrchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        gateway_classify: Callable,
        gateway_evaluate_policy: Callable,
        idem_log: IdempotencyLog | None,
        ledger: RunEventLedger | None,
        audit_sink: Callable[[dict], None] | None,
        approver: Approver | None,
        plan_approver: Callable | None,
        directory_requester: Callable | None,
        question_asker: Callable | None,
        interrupt_hooks: list[Callable[[], None]] | None,
        max_iterations: int,
        ttft_timeout: float | None,
    ) -> None:
        ...
```

Public API:
- `async def authorize_and_execute(self, tool_calls: list[ToolCall]) -> AsyncIterator[Event]`
- `async def resume(self, pending_calls: list[ToolCall]) -> AsyncIterator[Event]`
- `def set_cancellation_token(self, token: CancellationToken) -> None`

### TurnEngine Changes

- Remove `_handle_tool_calls`, `_execute_sync`, `_record_result`, `_authorize`, `resume`, `_unanswered_trailing_tool_calls`, `_parallel_safe`, `_interrupted_tool`, `_audit` (moved to orchestrator)
- Add `self._tool_lifecycle = ToolLifecycleOrchestrator(...)`
- `_loop()` calls `self._tool_lifecycle.authorize_and_execute(pending)`
- `resume()` calls `self._tool_lifecycle.resume(pending)`

### Cancellation Token

Introduce `CancellationToken` protocol (Python `asyncio.Event` wrapper for now; Rust will replace):
```python
class CancellationToken:
    def __init__(self) -> None: self._event = asyncio.Event()
    def cancel(self) -> None: self._event.set()
    async def wait(self) -> None: await self._event.wait()
    def is_cancelled(self) -> bool: return self._event.is_set()
```

### Event Types

Orchestrator uses existing `EventType` enum. No new event types.

## Migration Path

1. **ADR-034 (this)**: Python extraction — `core/tool_lifecycle.py` created, `engine.py` refactored.
2. **ADR-035**: Tool Lifecycle Orchestration hard-cut to Rust — `delta_core` gains a `toollifecycle.plan` command that owns the execution disposition (execute / replay / uncertain) and its paired `record_planned` + `mark_executing` transitions; Python `_execute_sync` becomes a thin caller.
3. **ADR-036**: Resume Decision audit — closed: authority already split between ADR-029 (checkpoint) and ADR-035 (toollifecycle.plan). No migration needed.
4. **ADR-037**: Cancellation / Timeout / Retry final audit — closed: all three are runtime guards or pure functions with no persisted authority value. R3 is complete.

## Rollback

Git revert. No data migration; in-memory orchestration only.

## References

- ADR-033: R3 Execution Lifecycle Plan
- ADR-022: Idempotency Hard-Cut (state machine already Rust)
- ADR-029: Checkpoint Hard-Cut (resume uses checkpoint)
- ADR-030: Policy Hard-Cut (gateway calls already Rust)
- `core/engine.py`: source of truth for current orchestration
- `core/idemlog.py`: idempotency facade (Rust-authoritative)