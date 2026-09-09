# ADR-031: R2 Approval Hard-Cut — Rust as Sole Audit Write Authority

**Status**: Accepted
**Date**: 2026-09-09
**Supersedes**: None (complements ADR-018, ADR-030)

## Context

The Approval domain in `core/engine.py` and `core/audit.py` had two responsibilities:

1. **Audit writing**: Persisting approval audit events (tool call stage, status, approval outcome, args, level, isolation) to the `audit_events` SQLite table.
2. **Interactive decision**: The `ApprovalOutcome` enum (`ONCE`, `ALWAYS_TOOL`, `ALWAYS_COMMAND`, `DENY`), `PermissionRequest` dataclass, and `Approver` callback — the runtime's interactive consent flow.

ADR-018 splits these: **audit writing** migrates to Rust (deterministic persistence, same schema); **interactive decision** stays in Python (requires LLM context, user interaction, session state).

The R2 Trusted Execution Plan (ADR-018) requires promoting Approval audit writing from a Python implementation to a Rust authority. This is the final domain in the R2 hard-cut sequence (Checkpoint → Policy → Approval → R2 Final Convergence).

## Decision

**Rust `delta_core` is now the sole Approval audit write authority.**

- Python performs **sanitization and payload construction only** (scrubbing secrets, resolving connector, extracting resource, truncating previews).
- The actual SQLite INSERT runs in Rust via `approval.record`.
- Python retains a thin facade (`core/approval.py`) that delegates to `DeltaCoreClient` — no local SQLite writes.
- The interactive approval decision (`ApprovalOutcome`, `PermissionRequest`, `Approver`) remains entirely in Python `engine.py`.
- Protocol version bumped: `PROTOCOL_VERSION` 8 → 9 (both Rust `delta_core.rs` and Python `delta_core_client.py`).

## New Rust Authority Surface

### Command (`delta_core.rs`)

| Command | Purpose |
|---------|---------|
| `approval.record` | Persist an approval audit event to `audit_events` table; returns `{id: i64, timestamp: String}` |

### Approval Schema

The audit schema is explicitly versioned (`APPROVAL_SCHEMA_VERSION = 1`). The `audit_events` table structure (unchanged from Python):

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    agent TEXT,
    workspace TEXT,
    connector TEXT,
    tool TEXT,
    stage TEXT,
    status TEXT,
    approval TEXT,
    args TEXT,
    result_preview TEXT,
    reason TEXT,
    resource TEXT,
    level TEXT DEFAULT '',
    isolation TEXT DEFAULT ''
)
```

Indexes: `idx_audit_session`, `idx_audit_tool`, `idx_audit_stage`, `idx_audit_timestamp`.

### Python Facade (`core/approval.py`)

Thin facade over `DeltaCoreClient` — no local SQLite write logic:

| Function | Delegates to | Fail-closed behavior |
|----------|--------------|----------------------|
| `record` | `approval.record` | Raises `ApprovalAuthorityError` (callers swallow in audit sinks) |

### Python Audit Store (`core/audit.py`)

`AuditStore.append()` now:
1. Performs sanitization (`_sanitize_args`, `_resource`, `_truncate`) — stays in Python
2. Delegates the actual INSERT to `core.approval.record()` → Rust
3. The `list()` method stays in Python (reads from the same SQLite file via its own connection)

### What Stays in Python

- `ApprovalOutcome` enum (`ONCE`, `ALWAYS_TOOL`, `ALWAYS_COMMAND`, `DENY`)
- `PermissionRequest` dataclass
- `Approver` callback and interactive consent flow
- `AuditStore.list()` read path
- Sanitization logic (`_sanitize_args`, `_summarize`, `_resource`, `_truncate`)

### Removed

- `AuditStore.append()` direct SQLite INSERT (now delegates to Rust via `core.approval.record()`)

## Hard-Cut Invariants (ADR-018 Compliance)

| Invariant | Verified |
|-----------|----------|
| No fallback / dual-write / shadow path | ✅ |
| Single Rust call for audit write | ✅ |
| Deterministic audit persistence | ✅ |
| Fail-closed on authority unavailability | ✅ |
| `scripts/check_rust_authority_migration.py` guard passes | ✅ |
| Interactive approval decision stays in Python | ✅ |

## Consequences

- **Audit writes are deterministic and authoritative**: the Rust authority is the single source of truth for `audit_events` INSERT.
- **Fail-closed**: if `delta_core` is unavailable, the audit event is not written (callers in `engine.py._audit()` and `manager_sessions.py` already swallow `Exception`).
- **Interactive approval unchanged**: the `ApprovalOutcome` / `PermissionRequest` / `Approver` flow in `engine.py` is untouched.
- **Read path unchanged**: `AuditStore.list()` reads from the same SQLite file via its own Python connection.
- **R2 Final Convergence**: all 6 R2 domains (Artifact / Source-Citation / Validation / Checkpoint / Policy / Approval) are now Rust-authoritative.

## Migration Notes

- `DELTA_RUST_AUTHORITY=approval` is now part of `RUST_WRITE_DOMAINS` — no config change required for users who already opted into Rust authority.
- `storage_authority.py` now lists `approval` in `RUST_WRITE_DOMAINS`.
- `core/approval.py` is a new thin facade (not a delegate wrapper — no opt-in needed).
- `core/audit.py` `append()` method updated to delegate to `core.approval.record()`.
- `PROTOCOL_VERSION` bumped to 9 (Python and Rust must agree).

## References

- ADR-002: Approval Taxonomy (L0–L4 taxonomy)
- ADR-018: R2 Trusted Execution Plan
- ADR-030: R2 Policy Hard-Cut (pattern precedent)
- ADR-031: This document

## Rollback

Git revert / release rollback only. No dual-authority period.
