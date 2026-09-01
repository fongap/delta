# ADR-002 Approval / Risk Model

**Status:** Active

## Context

Delta enforces a risk‑based approval model to prevent unintended side effects. The model defines risk classes L0–L4 and corresponding policies:
- **L0**: read‑only, no side effects.
- **L1**: reversible local writes (checkpointed).
- **L2**: consequential local writes / config changes.
- **L3**: external effects that are compensatable.
- **L4**: irreversible or sensitive actions that never auto‑allow.

The `core/gateway.py` implements deterministic classification (`classify`) based on:
- Tool metadata (`risk_level`, `requires_approval`, `category`).
- Irreversible tool table.
- Egress tools floor.
- Sensitive resource detection.

## Decision

`RiskLevel` enum now represents L0‑L4. Classification logic ensures:
- Fail‑closed for unknown metadata → L4.
- Egress tools always at least L3.
- Irreversible tools forced to L4.
- Sensitive resources escalates L3 → L4.
- No model output influences classification.

## Consequences

- Guarantees deterministic, auditable risk assessment.
- Aligns with `docs/approval-taxonomy-adr.md` referenced in tests.
- Provides clear contract for UI and policy configuration.
