# ADR-035: R3 Tool Lifecycle Orchestration Hard-Cut

- Status: Accepted
- Date: 2026-09-10
- Scope: R3 Execution Lifecycle Phase 2 — move the idempotency-driven execution disposition decision from Python to Rust
- Decision Type: Authority switch (execution orchestration)
- Related: ADR-033 (R3 Plan), ADR-034 (Engine Loop Restructuring), ADR-022 (Idempotency Hard-Cut), ADR-026 (Artifact Hard-Cut)

## Background

ADR-034 extracted the tool-call lifecycle into `core/tool_lifecycle.py` so the
engine's async streaming loop is separable from tool-call orchestration. The
orchestrator's execution path (`_execute_sync`) still drives the idempotency
state machine in Python, even though every persisted transition it makes is
already Rust-authoritative (ADR-022):

```text
lookup → (committed? → replay | uncertain? → surface | else)
          record_planned → mark_executing → registry.execute()
          → commit / mark_failed → (write tool? → register_artifact)
```

The **persisted state machine** is already Rust. What remains in Python is the
**disposition decision** — answering, for a given tool call, *"should this call
execute, be replayed, or surface for user resolution?"* and then performing the
`record_planned`/`mark_executing` transitions that must atomically precede
execution.

## Decision

Introduce a Rust **tool-lifecycle executor disposition** command that owns the
pre-execution decision and its paired persisted transitions. Python's
`_execute_sync` becomes a thin caller: it asks Rust for a disposition, then
either skips (replay/uncertain) or runs the tool and reports the outcome.

### Rust: `toollifecycle.plan`

New protocol command (in `delta_core`):

```json
{"cmd": "toollifecycle.plan", "db": "<side_effects.db>",
 "run_id": "...", "tool_call_id": "...", "tool_name": "...", "args": {...}}
```

Rust updates the idempotency row (reusing `IdempotencyWriter`) and returns a
disposition:

```json
{"ok": true, "result": {"action": "execute"}}
{"ok": true, "result": {"action": "replay",  "result": {...}}}
{"ok": true, "result": {"action": "uncertain", "result": {...}, "operation_id": "..."}}
```

Semantics (mirrors `_execute_sync`, `core/tool_lifecycle.py:273`):

1. `lookup(run_id, tool_call_id, args)`.
2. If an entry exists:
   - `uncertain` → `{action: "uncertain", result: {error, operation_id}}` (never auto-replayed).
   - `committed` (args match) → `{action: "replay", result: <stored result>}`.
3. Otherwise `record_planned` then `mark_executing`, return `{action: "execute"}`.

No new state table: `toollifecycle.plan` is a **read-decide-transition** wrapper
over the existing `IdempotencyWriter`, exactly one persisted transition pair per
new execution (RecordPlanned + MarkExecuting), identical to the Python path.

### Python: `_execute_sync` delegation

`ToolLifecycleOrchestrator._execute_sync` calls `toollifecycle.plan` first:

- `execute` → run `ctx.registry.execute()` (Python tool execution is **not**
  authority; tools, MCP, and connectors are Python capabilities), then
  `idem.commit`/`idem.mark_failed` (already Rust) and artifact registration
  (already Rust, ADR-026).
- `replay` → return the stored result, status `"replayed"`.
- `uncertain` → return the error, status `"uncertain"`.

`idem_log.lookup` / `record_planned` / `mark_executing` are **removed from the
Python execution path** in favor of the single Rust disposition call, so Python
can no longer re-order or skip transitions around actual tool execution.

## Authority Before / After

| Aspect | Before | After |
|--------|--------|-------|
| Dedup / replay decision | Python `_execute_sync` (via idem.lookup) | Rust `toollifecycle.plan` |
| Uncertain surfacing decision | Python `_execute_sync` | Rust `toollifecycle.plan` |
| record_planned + mark_executing sequencing | Python `_execute_sync` | Rust `toollifecycle.plan` (atomic with decision) |
| Tool execution (`registry.execute`) | Python | Python (capability, not authority) |
| commit / mark_failed | Rust `idem.*` (ADR-022) | Rust `idem.*` (unchanged) |
| Artifact registration | Rust `artifact.register` (ADR-026) | unchanged |

## Non-Goals

- **Tool execution** stays in Python — it is a capability (arbitrary Python
  tools / MCP / connectors), not a persisted-authority domain. Rust decides
  *whether* to execute; Python executes.
- **Authorization / approval** (`_authorize`, PermissionEngine, interactive
  approver) stays in Python — that is the interactive decision surface, handled
  by ADR-030 (policy) + ADR-031 (approval audit) but not migrated here.
- **Resume decision** (`unanswered_trailing_tool_calls`, which calls to replay
  from history) is ADR-036.
- **Cancellation / Timeout / Retry** are ADR-037/038/039.

## Rollback

Git revert. `toollifecycle.plan` reuses the existing `side_effects.db` schema
and `IdempotencyWriter`; there is no data migration and no new persisted state.
The Python `_execute_sync` delegation can be reverted to the previous
`lookup`+`record_planned`+`mark_executing` ordering without touching data.

## Tests

- Rust unit tests: `toollifecycle.plan` returns `execute` on first call,
  `replay` on committed, `uncertain` on uncertain.
- Cross-language test: Python idempotency behavior unchanged (a committed call
  is not re-executed; a first call executes exactly once).
- Authority guard: asserts `toollifecycle.plan` is the only path that performs
  record_planned+mark_executing in the Python execution path.

## References

- ADR-022: Idempotency Hard-Cut
- ADR-026: Artifact Hard-Cut
- ADR-033: R3 Execution Lifecycle Plan
- ADR-034: Engine Loop Restructuring
- `core/tool_lifecycle.py` `_execute_sync`
- `core/idemlog.py` (thin facade)
- `core/runtime-native/src/idemlog.rs`