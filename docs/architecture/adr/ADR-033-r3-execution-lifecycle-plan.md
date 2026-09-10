# ADR-033: R3 Execution Lifecycle — Plan and Boundaries

- Status: Accepted
- Date: 2026-09-10
- Scope: R3 Execution Lifecycle — plan and boundaries for migrating execution orchestration from Python to Rust
- Decision Type: Migration-phase planning
- Related: ADR-018 (R2 Plan), ADR-032 (R2 Final Convergence), `docs/governance/rust-core-migration.md`

## Background

R2 Final Convergence (ADR-032) has sealed all 6 Trusted Execution domains as Rust-authoritative:

| Domain | ADR | Rust Authority |
|--------|-----|----------------|
| Artifact Registry | ADR-026 | `artifact.register` |
| Source/Citation | ADR-027 | `source.register` / `citation.mark` / `citation.validate` |
| Validation | ADR-028 | `validation.run` / `validation.register` / `validation.eval` |
| Checkpoint | ADR-029 | `checkpoint.register` / `checkpoint.get` / `checkpoint.list` / `checkpoint.latest` / `checkpoint.validate` / `checkpoint.close` |
| Policy | ADR-030 | `policy.classify` / `policy.evaluate` |
| Approval | ADR-031 | `approval.record` |

**R3 differs from R2 structurally**: R2 domains are *authority switches* — each domain owns a persistent store and the switch moves the write authority from Python to Rust. R3 domains are *execution orchestration* — they control how tool calls flow through the engine, not what data is persisted. The hard-cut model does not apply directly.

## R3 Scope

From `docs/governance/rust-core-migration.md` §5:

- **Tool lifecycle**: planned → authorized → executing → committed/failed/uncertain
- **Retry**: transient-failure retry with backoff
- **Backoff**: wait logic between retries
- **Timeout**: TTFT (time-to-first-token), provider SDK, tool-level
- **Cancellation**: user stop / interrupt propagation through engine async flow
- **Worker restart**: delta_core subprocess lifecycle
- **Resume decision**: checkpoint-based resume orchestration
- **Side-effect safety**: idempotency log (already Rust-authoritative via ADR-022)

## Domain Audit

Based on production call-graph audit (see `docs/governance/rust-core-migration.md` for audit trail):

### Already Rust-Authoritative (No Migration Needed)

1. **Side-Effect Safety (IdempotencyLog)**: State machine `planned → executing → committed/failed/uncertain` fully hard-cut to Rust (ADR-022). Python `core/idemlog.py` is a thin facade. No additional migration needed.

2. **Checkpoint Authority**: `RecoveryStore` hard-cut to Rust (ADR-029). Python is a thin facade. No additional migration needed.

3. **Policy Evaluation**: `policy.classify` / `policy.evaluate` hard-cut to Rust (ADR-030). Python `core/gateway.py` is a thin facade.

4. **Approval Audit**: `approval.record` hard-cut to Rust (ADR-031). Python `core/approval.py` is a thin facade.

### No Authority Value — Do Not Migrate (Low Risk, No Value)

These domains have no persisted state, no authority question, and no integrity gain from Rust migration. Forcing a hard-cut would add subprocess round-trips to a hot path with zero benefit:

- **Backoff Math**: `backoff_delay()`, `wait_for_retry_async()` — pure functions, no side effects, no persisted state. Not a domain; it's a utility.
- **Worker Restart**: `DeltaCoreClient` is the Python-side host client that *spawns* delta_core. It is infrastructure, not a domain with authority. There is no separate "worker restart authority" to migrate.

### Requires Engine Restructuring (High Risk — Real R3 Work)

These domains own execution orchestration that lives in `core/engine.py`:

5. **Tool Lifecycle Orchestration**: `_handle_tool_calls()`, `_execute_sync()`, `engine.resume()`. These orchestrate the idempotency state machine but live in Python. Moving to Rust requires either:
   - Porting the turn engine to Rust, or
   - Introducing a Rust `ExecutionController` that owns the tool lifecycle end-to-end with Python as event source/sink.

6. **Resume Decision**: `engine.resume()` reconstructs unanswered trailing tool calls from message history and re-runs them. The decision logic (which calls to replay, when to stop) is deeply coupled to engine state. High risk to move without full engine restructuring.

### Requires Partial Restructuring (Medium Risk — Real R3 Work)

7. **Cancellation**: `_cancel` Event, `_interruptible()`, executor interrupt hooks. Pervasive in engine async flow but conceptually separable as a "cancellation token" service.

8. **Timeout (TTFT)**: `ttft_timeout` guard in `_astream()`. Races queue against cancel event. Tightly woven into the stream producer/consumer but could be exposed as a Rust-side deadline.

9. **Retry**: `max_retries`, `is_retryable()`, `wait_for_retry_async()` in `core/call_errors.py` + engine loop. Coupled to engine loop state (`_turn_retries`).

## Decision

R3 does **not** proceed as a single hard-cut phase. The only meaningful R3 work is **engine restructuring** — the engine loop owns the orchestration that all remaining R3 domains depend on.

### Phase 1 — Engine Loop Restructuring Prerequisite (ADR-034)

Before any R3 domain can be hard-cut, the engine execution loop must be restructured so that **tool-call orchestration is separable from the async streaming machinery**. This is a prerequisite for all remaining R3 domains.

The restructuring goal: extract the tool-call lifecycle (planned → authorized → executing → committed/failed/uncertain) from `engine.py` `_handle_tool_calls()` / `_execute_sync()` / `resume()` into a dedicated **Tool Lifecycle Orchestrator** that:

- Owns the idempotency state machine transitions (delegating to Rust `idemlog`)
- Owns the tool execution dispatch (concurrent vs serial)
- Owns the resume decision (which calls to replay from checkpoint)
- Exposes a clean async interface to the engine loop

### Phase 2 — Remaining Domains (Post-Restructuring)

Once the engine loop is restructured and the Tool Lifecycle Orchestrator exists:

- **Tool Lifecycle Orchestration** (ADR-035): the orchestrator becomes Rust-authoritative; Python engine becomes event source/sink.
- **Resume Decision** (ADR-036): Rust decides which tool calls to replay from checkpoint.
- **Cancellation** (ADR-037): Rust `CancellationToken` replaces Python `_cancel` Event.
- **Timeout (TTFT)** (ADR-038): Rust deadline enforcement in stream producer.
- **Retry** (ADR-039): Rust retry policy engine.

## Non-Goals for R3

- **Backoff migration** — pure utility, no authority value
- **Worker restart migration** — infrastructure, no authority value
- **Provider protocol migration** (R5)
- **Scheduler / Automation Runtime** (R4)
- **Capability Worker主体** (R4)
- **Multi-agent orchestration** (R4)
- **Memory expansion** (R4)
- **UI redesign** (N/A)

## Governance

Each R3 domain must follow `docs/governance/rust-core-migration.md` §6 单领域迁移流程. **Phase 1** (Engine Loop Restructuring) is a single ADR with a single PR — it is a prerequisite, not a domain migration.

## References

- ADR-018: R2 Trusted Execution Plan
- ADR-032: R2 Final Convergence
- `docs/governance/rust-core-migration.md` §5 R3
- `packages/delta_core_client.py`: DeltaCoreClient lifecycle
- `core/call_errors.py`: backoff/retry taxonomy
- `core/engine.py`: execution loop, tool lifecycle orchestration
- `core/idemlog.py`: side-effect state machine facade
- `core/runtime-native/src/idemlog.rs`: Rust idempotency authority
