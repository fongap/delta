# ADR-036: R3 Resume Decision — Audit and Closure

- Status: Accepted
- Date: 2026-09-10
- Scope: R3 Execution Lifecycle Phase 2 — audit the Resume Decision domain and close it
- Decision Type: Audit / No-migration decision
- Related: ADR-029 (Checkpoint Hard-Cut), ADR-035 (Tool Lifecycle Hard-Cut), ADR-033 (R3 Plan)

## Background

ADR-033 listed "Resume Decision" as a high-risk R3 domain:

> Resume Decision (ADR-036): Rust decides which tool calls to replay from checkpoint.

The assumption was that the resume decision — determining which tool calls
to replay when a turn is resumed after a crash or engine eviction — required
a new Rust authority to own the replay-eligibility logic.

## Audit

The resume flow in production is:

```text
_durable_resume (manager_inbox.py)
  → runtime.resume() (engine.py)
    → unanswered_trailing_tool_calls() (tool_lifecycle.py)
    → authorize_and_execute(pending) (tool_lifecycle.py)
      → toollifecycle.plan (ADR-035: dedup / replay / uncertain)
```

### Already Rust-Authoritative

1. **Checkpoint persistence and validation** (ADR-029): `RecoveryStore` is
   a thin facade over Rust `checkpoint.register` / `checkpoint.get` /
   `checkpoint.validate`. The Rust authority owns the snapshot schema,
   `snapshot_hash`, and `recoverable` flag.

2. **Replay / dedup / uncertain decision** (ADR-035): `toollifecycle.plan`
   is the sole authority for whether a tool call executes, replays a
   committed result, or surfaces an uncertain prior attempt. This covers
   the entire "which tool calls to replay" decision.

### No Authority Value — Do Not Migrate

3. **`unanswered_trailing_tool_calls()`** reconstructs the list of
   unanswered tool calls from the in-memory `messages` list (Python
   conversation history). It is a pure data-extraction function:
   - No persisted state
   - No authority question (dedup is already Rust via ADR-035)
   - No data-consistency judgment
   - No cross-process or cross-thread visibility requirement

   Moving this to Rust would require serializing the entire `messages`
   list to the `delta_core` subprocess, having Rust parse OpenAI-shaped
   tool-call JSON, and returning a list back — adding a round-trip to a
   hot path with zero integrity gain. This mirrors the ADR-033 audit
   conclusion for Backoff (pure math) and Worker Restart (infrastructure).

4. **Checkpoint-based resume orchestration** (`RecoveryStore.get()` /
   `validate()`) is already Rust-authoritative (ADR-029). The
   `recoverable` flag, `snapshot_hash`, and phase validation are all
   decided by Rust. No additional migration is needed.

## Decision

**ADR-036 is an audit closure, not a migration.** The Resume Decision
domain has no remaining authority to hard-cut:

- Replay/dedop decision → already Rust (ADR-035 `toollifecycle.plan`)
- Checkpoint persistence/validation → already Rust (ADR-029)
- `unanswered_trailing_tool_calls()` → pure Python parsing, no authority value

No code changes. No new Rust module. No new `delta_core` command.

## Non-Goals

- Moving `unanswered_trailing_tool_calls()` to Rust — no authority value.
- Changing the resume orchestration flow — it works correctly with the
  existing split.

## Rollback

N/A (no code changes).

## References

- ADR-029: Checkpoint Hard-Cut
- ADR-033: R3 Execution Lifecycle Plan
- ADR-035: Tool Lifecycle Orchestration Hard-Cut
- `core/tool_lifecycle.py`: `unanswered_trailing_tool_calls`, `resume`
- `core/recovery.py`: `RecoveryStore` (Rust-authoritative facade)
- `services/server/manager_inbox.py`: `_durable_resume`