# ADR-038: Timeout Decision Authority

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-10 |
| **Supersedes** | Partial — extends the interruption authority introduced in [ADR-037](ADR-037-r3-cancellation-decision-authority.md) to cover the timeout cause. |
| **Related** | [ADR-035](ADR-035-r3-tool-lifecycle-hard-cut.md), [ADR-037](ADR-037-r3-cancellation-decision-authority.md) |

## Context

The master migration directive (§8) requires that Rust own the **timeout
decision authority**: when a tool call exceeds its execution deadline, Rust
must decide whether the side effect is Failed or Uncertain — Python must
never make that determination locally.

Before this ADR, tool execution had no deadline mechanism. The TTFT
(first-token) ceiling applied only to provider stream stalls, not to tool
calls. A tool that hung indefinitely could only be stopped by a user-initiated
cancel. The lifecycle state-machine decision for an interrupted tool call was
already Rust-authoritative (ADR-037), but only the cancellation cause was
wired.

## Decision

**Rust is the single authority for the interruption lifecycle-state decision.**
The existing `toollifecycle.cancel` command is extended with an optional
`reason` field so that timeout and user-stop share the same state-machine
decision while remaining distinguishable in the audit trail.

### Authority split

| Layer | Responsibility |
|---|---|
| **Rust** (authority) | State-machine transition: Executing → Uncertain, Planned → Failed, terminal → no-op. The `reason` parameter ("user_stop", "timeout") is an audit label; it does not change the decision. |
| **Python** (capability) | Deadline tracking: `tool_timeout` config, `ThreadPoolExecutor` with `future.result(timeout=...)`. When the deadline fires, Python calls `idem_log.cancel(reason="timeout")` and surfaces the Rust decision. |

### Key invariant

**A tool that times out while Executing is NEVER marked Failed.** The side
effect may or may not have occurred — only Uncertain is safe. This is the same
invariant as cancellation (ADR-037); timeout is simply a different interruption
cause with the same state-machine consequence.

### Protocol changes

- `toollifecycle.cancel` gains an optional `reason: Option<String>` field.
- Protocol version bumped 10 → 11.
- `ToolLifecycleCancelInput` carries `reason`; `mark_failed` uses it in the
  recorded error message ("timeout before execution" vs "cancelled before
  execution").

### Python facade

- `IdempotencyLog.cancel()` accepts `reason` (default "user_stop").
- `ToolLifecycleOrchestrator._timed_out_tool()` calls `cancel(reason="timeout")`.
- `ToolLifecycleOrchestrator._interrupted_tool()` calls `cancel(reason="user_stop")`.
- Both delegate to a shared `_interrupt_tool()` method — the state-machine
  decision is identical; only the audit reason differs.
- `_execute_sync()` wraps `registry.execute()` in a
  `ThreadPoolExecutor` with `future.result(timeout=tool_timeout)` when
  `tool_timeout > 0`.

### Config

New `tool_timeout` field in `packages/config.py` (default 300s). `<=0`
disables the guard.

## Consequences

- **One domain, one authority:** The interruption lifecycle-state decision
  (cancel or timeout) lives behind a single Rust command. No dual authority.
- **Audit clarity:** Operators can distinguish "timeout" from "user stop" in
  the ledger and audit trail without the state-machine decision diverging.
- **Protocol bump:** Clients must agree on version 11; mismatched clients
  fail-closed (same handshake guard as all prior bumps).
- **No new Rust command:** The state-machine decision for timeout is
  identical to cancellation; adding a separate `toollifecycle.timeout`
  command would duplicate logic without authority value.
