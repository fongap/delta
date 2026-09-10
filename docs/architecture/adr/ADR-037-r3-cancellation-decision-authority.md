# ADR-037: R3 Cancellation Decision Authority

- Status: Accepted
- Date: 2026-09-10
- Scope: R3 Execution Lifecycle — cancellation lifecycle-state decision authority
- Decision Type: Authority switch (cancellation lifecycle decision)
- Related: ADR-022 (Idempotency Hard-Cut), ADR-033 (R3 Plan), ADR-035 (Tool Lifecycle Hard-Cut)
- Supersedes: The cancellation portion of the prior audit-only ADR-037 (which incorrectly concluded "no authority value")

## Background

The prior ADR-037 (merged as #160) audited Cancellation / Timeout / Retry and
concluded all three had "no authority value, don't migrate." That conclusion
was **wrong** for Cancellation.

The error: conflating "the asyncio.Event signal transport is a pure runtime
flag" with "the cancellation lifecycle decision has no authority." The
asyncio.Event is indeed a runtime signal — but the **decision** of what
lifecycle state results from a cancel (Uncertain vs Failed) is a
control-plane authority with data-integrity consequences.

## Correct Audit

When a tool call is cancelled after execution has started (the side effect
may or may not have occurred), the idempotency row is in `Executing` state.
The decision: **mark it Uncertain** (never Failed, unless provably not
started). This is not a "pure runtime guard" — it is a **persisted
state-machine transition** that survives crash and determines user-visible
recovery behavior.

The master directive (§7) is explicit:

> 取消发生在 side effect executing 后、结果未知时 → Uncertain
> 而不是 Failed
> 除非能够证明 side effect 未发生

### Authority Before (Python)

`_interrupted_tool` in `core/tool_lifecycle.py` hardcoded `"interrupted"`
status for every cancelled call, regardless of whether the side effect had
started. It never consulted the idempotency state machine. An
`Executing` row stayed `Executing` after cancel — neither `Uncertain` nor
`Failed`.

### Authority After (Rust)

New `toollifecycle.cancel` command in `delta_core`:

```json
{"cmd": "toollifecycle.cancel", "db": "...",
 "run_id": "...", "tool_call_id": "...", "tool_name": "..."}
```

Rust decides based on the current idempotency state:

| State | Decision | Transition |
|-------|----------|------------|
| Executing | **Uncertain** | `mark_uncertain` — side effect may have occurred |
| Planned | **Failed** | `mark_failed` — nothing executed, safe to fail |
| Committed | **Terminal** | no transition — already settled |
| Failed | **Terminal** | no transition |
| Uncertain | **Terminal** | no transition |
| No row | **None** | no idempotency authority; Python decides locally |

Python's `_interrupted_tool` calls `idem_log.cancel()` and reflects Rust's
decision in the event status. It no longer hardcodes `"interrupted"`.

## What Stays in Python

The **signal transport** — `CancellationToken` wrapping `asyncio.Event`,
`request_interrupt()` setting the flag, `_interruptible()` racing the cancel
wait against the task — stays in Python. This is the cooperative signal
mechanism, not the authority. The authority is Rust's lifecycle-state
decision.

## Non-Goals

- Timeout decision authority → ADR-038 (future)
- Retry policy decision authority → ADR-039 (future)
- The `asyncio.Event` signal transport itself is not migrated to Rust

## Rollback

Git revert. `toollifecycle.cancel` reuses the existing `IdempotencyWriter`
(no new table, no new persisted state). Python reverts to hardcoding
`"interrupted"` status.

## Tests

- Rust: `cancel_executing_marks_uncertain`, `cancel_planned_marks_failed`,
  `cancel_committed_is_terminal`, `cancel_nonexistent_row_is_none`
- Python: existing `test_side_effect_ledger_contract.py` and
  `test_engine_stop.py` pass unchanged (the authority switch is transparent
  to the event-level contract)

## References

- ADR-022: Idempotency Hard-Cut
- ADR-033: R3 Execution Lifecycle Plan
- ADR-035: Tool Lifecycle Orchestration Hard-Cut
- `core/tool_lifecycle.py`: `_interrupted_tool`
- `core/idemlog.py`: `IdempotencyLog.cancel()`
- `core/runtime-native/src/tool_lifecycle.rs`: `cancel()`