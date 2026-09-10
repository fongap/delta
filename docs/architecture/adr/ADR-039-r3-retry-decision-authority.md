# ADR-039: Retry Policy Decision Authority

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-10 |
| **Supersedes** | Partial — supersedes the Retry portion of the ADR-037 audit (which incorrectly concluded "no authority value"). |
| **Related** | [ADR-033](ADR-033-r3-execution-lifecycle-plan.md), [ADR-037](ADR-037-r3-cancellation-decision-authority.md), [ADR-038](ADR-038-r3-timeout-decision-authority.md) |

## Context

The master migration directive (§9) requires that Rust own the **retry
policy decision authority**: whether a provider failure is retryable, and
which error class it belongs to. Python must not make that decision locally.

Before this ADR, the entire retry pipeline lived in Python's
`core/call_errors.py`:

- `classify_error(exc)` — classified provider exceptions into 8 error classes
  via isinstance checks and text matching.
- `is_retryable(exc)` — decided retryability based on the classification.
- `backoff_delay(attempt)` — computed the exponential backoff delay.
- `wait_for_retry_async(attempt, exc)` — async sleep + Retry-After extraction.
- `extract_retry_after(exc)` — parsed Retry-After headers from exceptions.

The prior ADR-037 audit concluded that Retry had "no authority value" and
should not be migrated. This was incorrect for the **decision** portion: the
classification and retryability determination is the policy authority that
controls whether the engine retries or gives up.

## Decision

**Rust is the single authority for retry policy classification.** A new
`retry.classify` command takes the error type name, error message text, and
a context-overflow flag from Python, and returns the error class + retryability.

### Authority split

| Layer | Responsibility |
|---|---|
| **Rust** (authority) | Error classification (`classify_error`) and retryability decision (`is_retryable`). Text-matching logic for auth, rate-limit, protocol-incompatible, stream-truncation, and transient markers lives in Rust. |
| **Python** (capability) | `backoff_delay()` pure math, `wait_for_retry_async()` async sleep, `extract_retry_after()` header parsing, retry loop, budget tracking (`_turn_retries`, `max_retries`), the actual re-stream execution. |

### Key invariant

**The decision of whether an error is retryable is Rust-authoritative.**
Python passes the exception's type name and message text; Rust classifies
and returns the decision. Python's retry loop checks `retryable` from the
Rust response alongside its own runtime state (budget, streamed, cancel).

### Rust implementation

New `retry.rs` module with `classify_error()` function:
- Input: `RetryClassifyInput { error_type, error_message, is_context_overflow }`
- Output: `RetryClassifyOutput { error_class, retryable }`
- Logic mirrors Python's prior `classify_error()`, but operates on strings
  rather than Python isinstance checks.
- Known exception class names (StreamTruncatedError, TTFTTimeoutError,
  ProtocolIncompatibleError) are matched by name.
- Context overflow is a flag from Python (Rust trusts it).

### Protocol changes

- New `retry.classify` command.
- Protocol version bumped 11 → 12.

### Python facade

- `call_errors.py` `classify_error()` and `is_retryable()` delegate to Rust
  via `default_client().command({"cmd": "retry.classify", ...})`.
- `backoff_delay()`, `wait_for_retry_async()`, `extract_retry_after()`
  stay in Python as pure capabilities.
- The local text-matching logic (`_PROTOCOL_MARKERS` etc.) is deleted.

## Consequences

- **One domain, one authority:** The retry policy decision lives behind a
  single Rust command. No dual authority, no local fallback.
- **Pure computation:** `retry.classify` needs no DB access — it's a pure
  function over its inputs, so the call is fast.
- **Protocol bump:** Clients must agree on version 12; mismatched clients
  fail-closed.
- **Backoff math stays Python:** The delay calculation (`backoff_delay`)
  and the actual sleep (`wait_for_retry_async`) are capabilities, not
  authority. They remain in Python.
