# ADR-029: R2 Checkpoint Hard-Cut — Rust as Sole Checkpoint Authority

**Status**: Accepted
**Date**: 2026-09-09
**Supersedes**: None (complements ADR-019, ADR-018)

## Context

ADR-019 introduced a Rust shadow reader for Checkpoint (`core/runtime-native/src/checkpoint.rs`) that cross-checks the Python implementation in `core/recovery.py:RecoveryStore`. The Python code remained the write authority, persisting recovery snapshots as a JSON sidecar file (`recovery-snapshots.json`).

The R2 Trusted Execution Plan (ADR-018) requires promoting Checkpoint from a shadow reader to a full write authority. This ADR documents the hard-cut that makes Rust `delta_core` the **sole Checkpoint authority**: snapshot registration, retrieval, validation, and persistence.

## Decision

**Rust `delta_core` is now the sole Checkpoint trusted authority.**

- Python performs **extraction and candidate construction only** (collecting runtime/session state, building the snapshot dict).
- All trusted facts are persisted as ledger events in `run_events.db` via the unified `delta_core` process. Event type:
  - `checkpoint.registered` — checkpoint snapshot registered for a run/session
- No Python fallback, dual-write, shadow production path, or migration delegate remains.
- Protocol version bumped: `PROTOCOL_VERSION` 6 → 7 (both Rust `delta_core.rs` and Python `delta_core_client.py`).

## New Rust Authority Surface

### Commands (`delta_core.rs`)

| Command | Purpose |
|---------|---------|
| `checkpoint.register` | Persist a checkpoint snapshot (`checkpoint.registered` event) |
| `checkpoint.get` | Retrieve a checkpoint record by ID |
| `checkpoint.list` | List all checkpoint records, optionally filtered by `run_id` / `session_id` |
| `checkpoint.latest` | Get the latest checkpoint for a `run_id` |
| `checkpoint.validate` | Validate a checkpoint (schema, hash, structure) |
| `checkpoint.close` | Close the SQLite handle for this checkpoint DB in the cache |

### Schema

The checkpoint schema is explicitly versioned (`CHECKPOINT_SCHEMA_VERSION = 1`):

```json
{
  "id": "uuid-v4",
  "run_id": "string",
  "session_id": "string",
  "schema": 1,
  "created_at": "ISO8601 UTC",
  "phase": "running | awaiting_approval | awaiting_question | awaiting_directory | awaiting_plan",
  "pending_tool_call": {"id": "string", "name": "string", "args_preview": "string"} | null,
  "pending_inbox_item_id": "string | null",
  "last_event_seq": "integer | null",
  "todo_summary": [{"content": "string", "status": "string", "active_form": "string"}],
  "recent_artifacts": [{"path": "string", "kind": "string"}],
  "error": "string | null",
  "snapshot_hash": "sha256 hex",
  "recoverable": "boolean"
}
```

- **Canonical serialization**: sorted keys, compact separators (`canonical_json` mirrors Ledger).
- **Integrity**: `snapshot_hash = sha256(canonical_json(payload))` where payload excludes `id`, `snapshot_hash`, `recoverable`.
- **Recoverable flag**: true iff `phase` is one of the awaiting phases (user action required).

### Persistence Model

- Reuses existing `LedgerWriter` + `run_events.db` (no new DB).
- `run_id` = the actual run ID (scoped to the run, not a reserved namespace).
- Writer: `CheckpointWriter` in `checkpoint.rs`.
- Reader: `CheckpointReader` in `checkpoint.rs` (replays `checkpoint.registered` events).

### Python Facade (`core/recovery.py`)

Thin facade over `DeltaCoreClient` — no local persistence, no final verdicts:

| Function | Delegates to |
|----------|--------------|
| `write` | `checkpoint.register` |
| `get` / `get_by_run` / `get_by_id` | `checkpoint.latest` / `checkpoint.get` |
| `latest` | `checkpoint.list` |
| `validate` | `checkpoint.validate` |
| `clear` | no-op (returns `True` for compat; checkpoints are append-only) |
| `close` | `checkpoint.close` |

All authority failures raise `CheckpointAuthorityError` (fail-closed).

### Removed

- `core/recovery.py` JSON sidecar writer (`load_json_state` / `save_json_state` / `recovery-snapshots.json`).
- `inspect_checkpoint` binary (was ADR-019 diagnostic tool, not production authority).
- `RUST_READ_DOMAINS` entry for `checkpoint` (now a write authority).
- `is_rust_shadow_reader("checkpoint")` API path (raises `InvalidAuthorityTargetError`).
- Shadow-reader cross-check path.

## Hard-Cut Invariants (ADR-018 Compliance)

| Invariant | Verified |
|-----------|----------|
| No fallback / dual-write / shadow path | ✅ |
| No new DB (reuses `run_events.db`) | ✅ |
| Canonical JSON + sha256 integrity | ✅ |
| Idempotent register (same id + same hash = success) | ✅ |
| Conflict on same id + different hash | ✅ |
| `scripts/check_rust_authority_migration.py` guard passes | ✅ |

## Legacy Data Compatibility

- **No Python runtime authority compatibility**: The old JSON sidecar format is NOT read by the new Rust authority. The authority cut is hard.
- **Data compatibility**: If users have existing `recovery-snapshots.json` files, a separate one-time migration tool can be written to replay them into the ledger (not in scope for this ADR — the authority switch is immediate).
- **Principle**: Data Compatibility ≠ Authority Compatibility.

## Consequences

- **Checkpoint facts are durable**: every `checkpoint.registered` event is hash-chained in the ledger.
- **Deterministic integrity**: the Rust implementation is the single source of truth for snapshot validity.
- **Fail-closed**: if `delta_core` is unavailable, checkpoint operations fail with `CheckpointAuthorityError` — no silent Python fallback.
- **Next domain**: Policy (per ADR-018 order: Source/Citation → Validation → Checkpoint → Policy → Approval).

## Migration Notes

- `DELTA_RUST_AUTHORITY=checkpoint` is now the **only** way to run checkpoint operations (no Python path).
- `storage_authority.py` now lists `checkpoint` in `RUST_WRITE_DOMAINS` — no config change required for users who already opted into Rust authority.
- Existing tests updated to exercise the Rust authority path.

## References

- ADR-005: Reliable Task Runtime (P3 §4.5 / §7.3: Recovery Context)
- ADR-018: R2 Trusted Execution Plan
- ADR-019: R2 Pre-Plumbing (shadow readers)
- ADR-028: R2 Validation Hard-Cut (pattern precedent)
- ADR-029: This document

## Rollback

Git revert / release rollback only. No dual-authority period.