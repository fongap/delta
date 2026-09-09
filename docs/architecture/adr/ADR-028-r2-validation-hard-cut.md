# ADR-028: R2 Validation Hard-Cut — Rust as Sole Validation Authority

**Status**: Accepted
**Date**: 2025-09-09
**Supersedes**: None (complements ADR-019, ADR-018)

## Context

ADR-019 introduced a Rust shadow reader for Validation (`validation.run` command in `delta_core`) that cross-checks the Python implementation in `core/validation.py:run_validation`. The Python code remained the write authority, persisting validation criteria and results to the run-event ledger via `manager._validate_run()`.

The R2 Trusted Execution Plan (ADR-018) requires promoting Validation from a shadow reader to a full write authority. This ADR documents the hard-cut that makes Rust `delta_core` the **sole Validation authority**: rule evaluation, verdicts, and persistence.

## Decision

**Rust `delta_core` is now the sole Validation trusted authority.**

- Python performs **extraction and candidate construction only** (artifact gathering, criteria object building).
- All trusted facts are persisted as ledger events in `run_events.db` via the unified `delta_core` process. Event types:
  - `validation.registered` — criteria + result registered for a run
- No Python fallback, dual-write, shadow production path, or migration delegate remains.
- Protocol version bumped: `PROTOCOL_VERSION` 5 → 6 (both Rust `delta_core.rs` and Python `delta_core_client.py`).

## New Rust Authority Surface

### Commands (`delta_core.rs`)

| Command | Purpose |
|---------|---------|
| `validation.register` | Persist a validation criteria + result (`validation.registered` event) |
| `validation.get` | Retrieve a validation record by ID |
| `validation.list` | List all validation records, optionally filtered by `run_id` |
| `validation.latest` | Get the latest validation for a `run_id` |
| `validation.eval` | Evaluate criteria against artifacts and persist the result |

### Persistence Model

- Reuses existing `LedgerWriter` + `run_events.db` (no new DB).
- `run_id` = the actual run ID (not a reserved namespace like sources).
- Writer: `ValidationWriter` in `validation.rs`.
- Reader: `ValidationReader` in `validation.rs` (replays `validation.registered` events).

### Python Facade (`core/validation.py`)

Thin facade over `DeltaCoreClient` — no local persistence, no final verdicts:

| Function | Delegates to |
|----------|--------------|
| `run_validation` | `validation.run` |
| `register_validation` | `validation.register` |
| `get_validation` | `validation.get` |
| `list_validations` | `validation.list` |
| `latest_validation` | `validation.latest` |
| `evaluate_and_register` | `validation.eval` |

All authority failures raise `ValidationAuthorityError` (fail-closed).

### Removed

- `core/validation_delegate.py` — migration delegate (no longer needed).
- `tests/test_validation_delegate.py` — delegate tests.
- Python `is_rust_authority("validation")` check inside `run_validation` — always goes through Rust now.

## Hard-Cut Invariants (ADR-018 Compliance)

| Invariant | Verified |
|-----------|----------|
| No fallback / dual-verdict / dual-write / shadow path | ✅ |
| No new DB (reuses `run_events.db`) | ✅ |
| No PDF/Office/OCR/email parsing moved into Rust | ✅ |
| `scripts/check_rust_authority_migration.py` guard passes | ✅ |

## Consequences

- **Validation verdicts are durable**: every `validation.registered` event is hash-chained in the ledger.
- **Deterministic rule engine**: the Rust implementation in `validation.rs` is the single source of truth for all checks (artifact count, completeness, required paths, size gates, substrings, CSV headers, citation floor).
- **Fail-closed**: if `delta_core` is unavailable, validation fails with `ValidationAuthorityError` — no silent Python fallback.
- **Next domain**: Checkpoint (per ADR-018 order: Source/Citation → Validation → Checkpoint → Policy → Approval).

## Migration Notes

- `DELTA_RUST_AUTHORITY=validation` is now the **only** way to run validation (no Python path).
- `storage_authority.py` already listed `validation` in `RUST_WRITE_DOMAINS` — no config change required.
- Existing tests updated to exercise the Rust authority path.

## References

- ADR-005: Reliable Task Runtime (WS3: Validation)
- ADR-018: R2 Trusted Execution Plan
- ADR-019: R2 Pre-Plumbing (shadow readers)
- ADR-027: R2 Source/Citation Hard-Cut (pattern precedent)