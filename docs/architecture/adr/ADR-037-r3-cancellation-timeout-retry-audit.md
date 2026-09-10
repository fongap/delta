# ADR-037: R3 Final Audit — Cancellation / Timeout / Retry Closure

- Status: **Superseded** (Cancellation portion superseded by ADR-037-r3-cancellation-decision-authority; Timeout portion superseded by ADR-038-r3-timeout-decision-authority)
- Date: 2026-09-10
- Scope: R3 Execution Lifecycle — final audit of the three remaining domains (Cancellation, Timeout, Retry)
- Decision Type: Audit / No-migration decision / Phase closure
- Related: ADR-033 (R3 Plan), ADR-035 (Tool Lifecycle Hard-Cut), ADR-036 (Resume Decision Audit)

> **Correction (2026-09-10)**: The Cancellation portion of this audit was
> wrong. Cancellation **does** have authority value — the lifecycle-state
> decision (Uncertain vs Failed) after a cancel is a persisted state-machine
> transition. See ADR-037-r3-cancellation-decision-authority.md for the
> corrected decision and implementation. The Timeout portion was also
> superseded by ADR-038 (timeout shares the same state-machine decision
> as cancellation). The Retry portion remains under review for ADR-039.

## Background

ADR-033 listed three remaining R3 domains as "Requires Partial Restructuring
(Medium Risk)":

7. **Cancellation**: `_cancel` Event, `_interruptible()`, executor
   interrupt hooks.
8. **Timeout (TTFT)**: `ttft_timeout` guard in `_astream()`.
9. **Retry**: `max_retries`, `is_retryable()`, `wait_for_retry_async()` in
   `core/call_errors.py` + engine loop.

## Audit

### 7. Cancellation — No Authority Value

`CancellationToken` wraps `asyncio.Event`. The cancel state is:

- **Pure runtime**: an in-memory flag, set by `request_interrupt()` from any
  thread. It does not survive a crash, and it is not persisted.
- **No crash-recovery role**: when the process crashes, the cancel state is
  irrelevant — the idempotency state machine (ADR-022/035) already knows
  which calls completed and which were interrupted (via `sweep_stale` →
  `Uncertain`).
- **No data-consistency judgment**: cancellation is a cooperative signal to
  stop the Python asyncio loop, not a persisted decision.

Migrating `CancellationToken` to Rust would require every `is_cancelled()`
check to round-trip the `delta_core` subprocess — adding latency to a hot
path (checked per tool call, per stream chunk, per approval prompt) with
zero integrity gain. The crash-safety guarantee already lives in the
idempotency authority.

### 8. Timeout (TTFT) — No Authority Value

The `ttft_timeout` guard races the provider stream against an `asyncio`
deadline. The decision ("did the first token arrive in time?") is:

- **Pure runtime**: the deadline is an `asyncio` timer, not a persisted
  fact. A crashed process has no pending timeout — the idempotency state
  machine decides what to replay.
- **No persisted state**: timeout outcomes (retry / surface error) are
  driven by the retry policy, which is itself a pure function (see below).

### 9. Retry — No Authority Value

`max_retries`, `is_retryable()`, `classify_error()`, `backoff_delay()`,
`wait_for_retry_async()` in `core/call_errors.py`:

- **Pure functions**: `classify_error()` is text matching;
  `backoff_delay()` is `base_ms * 2^attempt` with jitter — no side effects,
  no persisted state. ADR-033 already audited Backoff ("pure math, no
  authority value") — the retry policy is the same category.
- **`_turn_retries` is runtime state**: a counter reset each turn, not
  persisted. On crash, the idempotency state machine handles replay
  eligibility, not the retry counter.

## Decision

**ADR-037 is an audit closure.** All three remaining R3 domains
(Cancellation, Timeout, Retry) have no authority value:

| Domain | What it is | Persisted? | Authority value? |
|--------|-----------|-----------|-------------------|
| Cancellation | `asyncio.Event` cooperative signal | No | None |
| Timeout (TTFT) | `asyncio` deadline race | No | None |
| Retry | Pure math + text classification | No | None |

No code changes. No new Rust modules. No new `delta_core` commands.

## R3 Closure

R3 Execution Lifecycle is now **complete**:

| Domain | ADR | Outcome |
|--------|-----|---------|
| Side-Effect Safety (Idempotency) | ADR-022 | Already hard-cut (R1) |
| Checkpoint | ADR-029 | Already hard-cut (R2) |
| Policy | ADR-030 | Already hard-cut (R2) |
| Approval | ADR-031 | Already hard-cut (R2) |
| Engine Loop Restructuring | ADR-034 | Python extraction (Phase 1) |
| Tool Lifecycle Disposition | ADR-035 | Hard-cut: `toollifecycle.plan` |
| Resume Decision | ADR-036 | Audit: already covered by ADR-029 + ADR-035 |
| Backoff | ADR-033 | Audit: pure math, no authority value |
| Worker Restart | ADR-033 | Audit: infrastructure, no authority value |
| Cancellation | ADR-037 (this) | Audit: runtime signal, no authority value |
| Timeout (TTFT) | ADR-037 (this) | Audit: runtime deadline, no authority value |
| Retry | ADR-037 (this) | Audit: pure functions, no authority value |

The R3 authority surface is complete: the only authority domains —
idempotency state-machine disposition (`toollifecycle.plan`) and checkpoint
persistence/validation — are Rust-authoritative. Everything else is either
a Python capability (tool execution, interactive approval) or a runtime
guard (cancellation, timeout, retry) with no persisted authority role.

## Rollback

N/A (no code changes).

## References

- ADR-022: Idempotency Hard-Cut
- ADR-029: Checkpoint Hard-Cut
- ADR-033: R3 Execution Lifecycle Plan
- ADR-035: Tool Lifecycle Orchestration Hard-Cut
- ADR-036: Resume Decision Audit
- `core/tool_lifecycle.py`: `CancellationToken`, `_interruptible`
- `core/engine.py`: `_astream`, TTFT guard, retry loop
- `core/call_errors.py`: `classify_error`, `is_retryable`, `backoff_delay`