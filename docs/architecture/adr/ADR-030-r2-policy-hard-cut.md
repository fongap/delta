# ADR-030: R2 Policy Hard-Cut — Rust as Sole Policy Evaluation Authority

**Status**: Accepted
**Date**: 2026-09-09
**Supersedes**: None (complements ADR-018, ADR-029)

## Context

The Execution Gateway (slices 1–4) in `core/gateway.py` provided deterministic policy evaluation:
1. **Slice 1 (classify)**: L0–L4 risk classification from tool metadata + arguments
2. **Slice 2 (enforce_level)**: L4 (irreversible/sensitive) never auto-allowed
3. **Slice 3 (enforce_scope)**: Resource confinement for side-effectful calls (L1+)
4. **Slice 4a (restrict_grants)**: L3+ external effects require explicit approval or policy

These were implemented in Python and called from `core/engine.py`. The R2 Trusted Execution Plan (ADR-018) requires promoting Policy from a Python implementation to a Rust authority. Unlike Checkpoint/Validation/Source, Policy is an **evaluation/decision domain** (not data with a sha256) — it produces a decision, not a persisted fact. The hard-cut makes Rust `delta_core` the sole policy authority.

## Decision

**Rust `delta_core` is now the sole Policy evaluation authority.**

- Python performs **extraction and candidate construction only** (collecting tool metadata, PermissionEngine decision, workspace roots).
- All policy evaluation (classify + enforce_level + restrict_grants + enforce_scope) runs in a single Rust call via `policy.evaluate`.
- Python retains a thin facade (`core/gateway.py`) that delegates to `DeltaCoreClient` — no local classification logic, no final verdicts.
- Protocol version bumped: `PROTOCOL_VERSION` 7 → 8 (both Rust `delta_core.rs` and Python `delta_core_client.py`).

## New Rust Authority Surface

### Commands (`delta_core.rs`)

| Command | Purpose |
|---------|---------|
| `policy.classify` | Classify a tool call into L0–L4 (returns `level: i64`) |
| `policy.evaluate` | Full four-slice evaluation (classify + enforce_level + restrict_grants + enforce_scope); returns `{decision: Decision, level: i64}` |

### Policy Schema

The policy schema is explicitly versioned (`POLICY_SCHEMA_VERSION = 1`):

```json
{
  "tool_name": "string",
  "arguments": { "...": "..." } | null,
  "metadata": {
    "risk_level": "low | medium | high" | null,
    "requires_approval": boolean | null,
    "category": "string" | null,
    "capabilities": ["string"] | null
  } | null,
  "decision": {
    "allowed": boolean,
    "reason": "string",
    "needs_user": boolean,
    "rule": "string",
    "grant": "blanket | session | policy | string"
  },
  "level": 0..4,
  "workspace_root": "absolute path",
  "roots": [{"path": "absolute path", "writable": boolean}]
}
```

Output:
```json
{
  "decision": { "allowed": boolean, "reason": "string", "needs_user": boolean, "rule": "string", "grant": "string" },
  "level": 0..4
}
```

### Four Slices (executed in order, Rust-authoritative)

1. **Slice 1 — classify**: Deterministic L0–L4 from metadata + arguments
   - Irreversible tools (`send_email`) → L4
   - Egress tools (`web_fetch`, `web_search`, `browser_read_url`, `browser_open_url`) → L3 floor
   - Missing/unknown metadata → L4 (fail-closed)
   - Band: high→L3, medium+approval→L3 (external) or L2 (local), low→L1 (reversible write) or L0
   - Sensitivity: L3 touching sensitive resource (payroll, credentials, identity) → L4

2. **Slice 2 — enforce_level**: L4 downgrades any `allowed=true` to `allowed=false, needs_user=true`

3. **Slice 4a — restrict_grants**: L3+ with `grant != "policy"` downgrades to `allowed=false, needs_user=true`

4. **Slice 3 — enforce_scope**: L1+ declared targets must be under a writable root; read-only roots downgrade to ask; outside roots downgrade to ask

### Python Facade (`core/gateway.py`)

Thin facade over `DeltaCoreClient` — no local policy logic:

| Function | Delegates to | Fail-closed behavior |
|----------|--------------|----------------------|
| `classify` | `policy.classify` | Returns `RiskLevel.L4` |
| `evaluate_policy` | `policy.evaluate` | Returns denied decision + L4 |

Utility functions retained (not policy authority):
- `write_paths` — path extraction for PermissionEngine scoping
- `isolation_status` — audit display helper
- `RiskLevel` enum — type annotations

### Removed

- `core/gateway.py` local classification logic (`_VALID_METADATA_RISK`, `_LOCAL_CATEGORIES`, `_REVERSIBLE_WRITE_CATEGORIES`, `_SENSITIVE_TOKENS`, `_band_level`, `_APPLY_PATCH_FILE`, `_APPLY_PATCH_MOVE`, `_UNIFIED_DIFF_FILE`, `_PATCH_BLOB_ARG`, `_PATH_ARGS`, `_RESOURCE_ARGS`, `declared_resources`, `declared_targets`, `touches_sensitive_resource`, `enforce_level`, `restrict_grants`, `enforce_scope`).
- `is_rust_shadow_reader("policy")` API path (Policy is not a shadow-read domain).
- Shadow-reader cross-check path for policy.

## Hard-Cut Invariants (ADR-018 Compliance)

| Invariant | Verified |
|-----------|----------|
| No fallback / dual-write / shadow path | ✅ |
| Single Rust call for all four slices | ✅ |
| Deterministic classification + enforcement | ✅ |
| Fail-closed on authority unavailability | ✅ |
| `scripts/check_rust_authority_migration.py` guard passes | ✅ |

## Consequences

- **Policy decisions are deterministic and auditable**: the Rust authority is the single source of truth for all four slices.
- **Fail-closed**: if `delta_core` is unavailable, classification returns L4 and evaluation denies — no silent Python fallback.
- **Single call optimization**: all four slices execute atomically in Rust, avoiding multiple subprocess round-trips.
- **Next domain**: Approval (per ADR-018 order: Checkpoint → Policy → Approval → R2 Final Convergence).

## Migration Notes

- `DELTA_RUST_AUTHORITY=policy` is now the **only** way to run policy evaluation (no Python path).
- `storage_authority.py` now lists `policy` in `RUST_WRITE_DOMAINS` — no config change required for users who already opted into Rust authority.
- `core/engine.py` updated to call `gateway.evaluate_policy` once instead of three separate slice calls.
- Existing tests updated to exercise the Rust authority path.

## References

- ADR-002: Approval Taxonomy (L0–L4 taxonomy)
- ADR-018: R2 Trusted Execution Plan
- ADR-029: R2 Checkpoint Hard-Cut (pattern precedent)
- ADR-030: This document

## Rollback

Git revert / release rollback only. No dual-authority period.