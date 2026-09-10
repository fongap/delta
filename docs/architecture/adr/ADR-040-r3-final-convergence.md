# ADR-040: R3 Final Convergence

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-10 |
| **Related** | ADR-033 (R3 Plan), ADR-035 (Tool Lifecycle), ADR-036 (Resume Audit), ADR-037 (Cancellation), ADR-038 (Timeout), ADR-039 (Retry) |

## Context

R3 (Execution Lifecycle) was planned in ADR-033 as a phased migration of
tool-call lifecycle authority from Python to Rust. The initial audit
(ADR-037 original, merged as PR #160) incorrectly concluded that
Cancellation, Timeout, and Retry had no authority value. The master
migration directive corrected this: all three domains have **decision
authority** that must live in Rust — the state-machine transitions and
retryability classifications that affect persisted state.

This ADR records the final state after all corrections.

## R3 Domain Authority Matrix

| Domain | Rust Authority | Python Capability | ADR |
|---|---|---|---|
| Tool lifecycle disposition (execute / replay / uncertain) | `toollifecycle.plan` — decides disposition, performs `record_planned` + `mark_executing` | `_execute_sync` calls Rust, runs `registry.execute()`, reports outcome | ADR-035 |
| Cancellation lifecycle state | `toollifecycle.cancel` — Executing → Uncertain (never Failed), Planned → Failed, terminal → no-op | `CancellationToken` (asyncio.Event signal), `_interrupted_tool` delegates to Rust | ADR-037 revised |
| Timeout lifecycle state | `toollifecycle.cancel` with `reason="timeout"` — same state-machine decision as cancellation, distinct audit label | `ThreadPoolExecutor` deadline, `_timed_out_tool` delegates to Rust | ADR-038 |
| Retry policy decision | `retry.classify` — error classification + retryability (returns `error_class` + `retryable`) | `backoff_delay()` (pure math), `wait_for_retry_async()`, retry loop, budget tracking | ADR-039 |
| Resume decision | `toollifecycle.plan` (dedup/replay/uncertain) + `idem.sweep_stale` + `checkpoint.register/get/validate` | `unanswered_trailing_tool_calls()` (pure message-history parsing, no authority) | ADR-036 (audit: already covered) |
| Backoff delay | N/A (pure function, no authority value) | `backoff_delay()` — 200ms base, ×2, ±10% jitter, 8s cap | ADR-033 (audit: no migration) |
| Worker restart | N/A (infrastructure, no authority value) | Process management, reconnect logic | ADR-033 (audit: no migration) |
| Side-effect safety | `idem.*` — all state transitions persisted in `side_effects.db` via Rust | `IdempotencyLog` facade delegates all writes to Rust | ADR-022 (R1 hard-cut) |

## Protocol Version History

| Bump | Command Added | ADR |
|---|---|---|
| 9 → 10 | `toollifecycle.cancel` | ADR-037 revised |
| 10 → 11 | `toollifecycle.cancel` `reason` field | ADR-038 |
| 11 → 12 | `retry.classify` | ADR-039 |

Final protocol version: **12**.

## Key Correction

The original ADR-037 audit (PR #160) concluded that Cancellation, Timeout,
and Retry were "pure runtime guards or pure functions with no persisted
authority value." This was wrong for all three:

- **Cancellation**: the Executing → Uncertain transition is a persisted
  state-machine decision with crash-recovery consequences.
- **Timeout**: same state-machine decision as cancellation — a timed-out
  tool may have side effects in flight.
- **Retry**: the retryability classification (`rate_limit` vs
  `stream_truncated` vs `auth`) determines whether the engine retries or
  surfaces — a decision that affects the turn's state.

The distinction is: **pure functions** (backoff math, message parsing) stay
Python; **decision authority** (state transitions, retryability
classification) goes to Rust.

## Decision

**R3 is complete.** All execution-lifecycle decision authority is
Rust-authoritative. Python retains capability-plane responsibilities: tool
execution, signal transport, deadline mechanism, backoff math, message
history parsing.

No further R3 ADRs will be created. The next migration phase is R4
(Runtime).
