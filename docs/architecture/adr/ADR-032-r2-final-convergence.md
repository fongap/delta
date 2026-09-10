# ADR-032: R2 Final Convergence — Trusted Execution Sealed

- Status: Accepted
- Date: 2026-09-10
- Scope: R2 Trusted Execution final convergence — seal the phase, delete residual shadow-read infrastructure
- Decision Type: Migration-phase completion record
- Related: ADR-018 (R2 Plan), ADR-019 (R2 Pre-Plumbing), ADR-026/027/028/029/030/031 (per-domain hard-cuts), `docs/governance/rust-core-migration.md`

## Goal

R2 Final Convergence — complete the R2 Trusted Execution phase final closure. All 6 R2 domains are hard-cut to Rust `delta_core` as sole authority. Delete residual shadow-read infrastructure (`RUST_READ_DOMAINS`, `is_rust_shadow_reader`, `DELTA_RUST_READERS`) and shadow-read test scaffolding.

## R2 Domains Final State

| # | Domain | ADR | Authority | Deleted Legacy |
|---|--------|-----|-----------|----------------|
| 1 | Artifact Registry | ADR-026 | Rust `delta_core` (`artifact.register`) | `core/artifact_delegate.py`, `tests/test_artifact_delegate.py`, `DELTA_RUST_AUTHORITY=artifact` |
| 2 | Source/Citation | ADR-027 | Rust `delta_core` (`source.register` / `citation.mark` / `citation.validate`) | `core/source_citation_delegate.py`, `tests/test_source_citation_delegate.py` |
| 3 | Validation | ADR-028 | Rust `delta_core` (`validation.run` / `validation.register` / `validation.eval`) | `core/validation_delegate.py`, `tests/test_validation_delegate.py` |
| 4 | Checkpoint | ADR-029 | Rust `delta_core` (`checkpoint.register` / `checkpoint.get` / `checkpoint.list` / `checkpoint.latest` / `checkpoint.validate` / `checkpoint.close`) | `inspect_checkpoint` binary, `checkpoint` from `RUST_READ_DOMAINS` |
| 5 | Policy | ADR-030 | Rust `delta_core` (`policy.classify` / `policy.evaluate`) | `enforce_level` / `restrict_grants` / `enforce_scope` Python implementations |
| 6 | Approval | ADR-031 | Rust `delta_core` (`approval.record`) | `core/audit.py` direct SQLite write, `DELTA_RUST_AUTHORITY=approval` selector |

## Shadow-Read Infrastructure Removal

### Before

`packages/storage_authority.py` maintained a parallel shadow-read authority surface:

- `RUST_READ_DOMAINS`: frozenset of R2 domains with Rust readers that cross-checked Python state (empty since ADR-031, but infrastructure remained)
- `READER_ENV_VAR` (`DELTA_RUST_READERS`): env var to enable shadow readers
- `is_rust_shadow_reader(domain)`: API to query reader enablement
- `tests/test_r2_shadow_read.py`: 24+ cross-language shadow-read tests invoking `inspect_artifact` / `inspect_validation` / `inspect_citation` binaries

### After

Shadow-read infrastructure is deleted:

- `RUST_READ_DOMAINS` deleted
- `READER_ENV_VAR` (`DELTA_RUST_READERS`) deleted
- `is_rust_shadow_reader()` deleted
- `tests/test_r2_shadow_read.py` deleted (shadow-read premise no longer valid post-hard-cut)

`RUST_WRITE_DOMAINS` remains as the sole authority declaration surface.

### Rationale

All 6 R2 domains are now hard-cut write authorities. The shadow-read phase (ADR-019 Pre-Plumbing) served its purpose during migration: verify Rust readers could correctly interpret Python-written state before cutover. Post-hard-cut, there is no "Python authority to cross-check against" — Rust *is* the authority. Retaining shadow-read infrastructure implies a dual-authority path that no longer exists.

## Authority Matrix (R2 Final Convergence)

| Domain | Authority |
|--------|-----------|
| Task Identity | Rust |
| Run Identity | Rust |
| Run State | Rust Ledger |
| Ledger | Rust |
| Idempotency | Rust |
| Task persistence | Rust |
| Run persistence | Rust |
| Storage coordination | No separate authority |
| Artifact Registry | Rust |
| Source/Citation | Rust |
| Validation | Rust |
| Checkpoint | Rust |
| Policy | Rust |
| Approval | Rust |
| Python fallback | None |

## Data Compatibility

Historical data compatibility is preserved. Existing SQLite files (`run_events.db`, `conversations.db`, `idemlog.db`, `audit_events.db`) remain readable by the Rust authorities. No data migration is required; the hard-cut changes only the runtime authority, not the on-disk format.

## Failure Behavior

- Rust Core unavailable → fail-closed (`DeltaCoreError` / `CheckpointAuthorityError` / domain-specific error)
- No Python fallback writer for any R2 domain
- Rollback: Git revert / release rollback only

## Rollback

Git revert / release rollback only. No dual-authority period, no runtime switch.

## Non-goals

- R3 Execution Lifecycle (tool lifecycle, retry, timeout, cancellation, scheduler)
- R4 Runtime (task execution, workflow lifecycle, automation runtime)
- R5 Provider Core
- Rust cross-DB transaction
- 2PC
- New unified DB
- Capability ABI expansion
- UI redesign
