"""Execution Gateway — the single classification point for every side effect.

Slice 1 (observe): every authorized tool call is classified into the L0–L4 risk
taxonomy (docs/approval-taxonomy-adr.md) BEFORE execution, and the level rides on
the audit trail. Allow/deny behavior is unchanged in this slice — the gateway is
where the later policy pipeline (schema validation → resource guard → risk class →
approval policy → sandbox) attaches, one stage per slice.

Fail-closed rule: a call that cannot be classified (no registry metadata, unknown
risk_level value, or an explicit irreversible-list hit) is treated as L4. Nothing
is ever "unclassified, so probably fine".
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Optional


class RiskLevel(IntEnum):
    L0 = 0  # read-only, no side effects
    L1 = 1  # reversible local writes (checkpointed)
    L2 = 2  # consequential local writes / config changes
    L3 = 3  # external effects, compensatable
    L4 = 4  # irreversible or sensitive — never auto-allowed

    @property
    def label(self) -> str:
        return self.name


# Tools whose effect is irreversible regardless of metadata. Extend deliberately:
# an entry here means "never auto-allowed, always an explicit human decision".
IRREVERSIBLE_TOOLS: frozenset[str] = frozenset(
    {
        "send_email",
    }
)

_VALID_METADATA_RISK = {"low", "medium", "high"}


def classify(
    tool_name: str,
    arguments: Optional[dict[str, Any]] = None,
    metadata: Any = None,
) -> RiskLevel:
    """Deterministic L0–L4 classification for one tool call. Never raises; an
    unclassifiable call classifies as L4."""
    if tool_name in IRREVERSIBLE_TOOLS:
        return RiskLevel.L4

    if metadata is None:
        return RiskLevel.L4

    risk = str(getattr(metadata, "risk_level", "") or "").lower()
    requires_approval = bool(getattr(metadata, "requires_approval", False))

    if risk not in _VALID_METADATA_RISK:
        return RiskLevel.L4

    if risk == "high":
        # Arbitrary local execution (shell): consequential and unsandboxed today,
        # so it sits at L3 — allowed only with an explicit per-run grant path.
        # It does NOT default to L4 because interactive use legitimately runs it.
        return RiskLevel.L3

    if risk == "medium":
        # Connector/integration calls with real external effects.
        return RiskLevel.L3 if requires_approval else RiskLevel.L2

    # risk == "low"
    return RiskLevel.L2 if requires_approval else RiskLevel.L0
