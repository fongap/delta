"""Execution Gateway — the single classification point for every side effect.

Slice 1 (observe): every authorized tool call is classified into the L0–L4 risk
taxonomy (docs/approval-taxonomy-adr.md) BEFORE execution, and the level rides on
the audit trail. Slice 2 (policy): L4 is never auto-allowed — an irreversible call
downgrades any rule-based allow to an explicit human decision. Slice 3 (guard):
declared on-disk targets of side-effectful calls must land inside the session's
trusted roots, whatever rule allowed the call — the choke point re-checks
confinement itself instead of trusting upstream classifiers to stay correct.
Slice 4a (grants): L3 external effects are never released by a blanket mode grant
or an approval-card-minted session entry — only explicit per-action approval or
user-authored standing policy gets through.

Fail-closed rule: a call that cannot be classified (no registry metadata, unknown
risk_level value, or an explicit irreversible-list hit) is treated as L4. Nothing
is ever "unclassified, so probably fine".
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
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

# Metadata categories whose medium-risk, approval-gated tools are LOCAL effects
# (checkpointed file writes), not external ones — they sit at L2, not L3. An
# unknown or missing category conservatively stays at L3 (fail closed → ask).
_LOCAL_CATEGORIES = frozenset({"filesystem"})


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
    category = str(getattr(metadata, "category", "") or "").lower()

    if risk not in _VALID_METADATA_RISK:
        return RiskLevel.L4

    if risk == "high":
        # Arbitrary local execution (shell): consequential and unsandboxed today,
        # so it sits at L3 — allowed only with an explicit per-run grant path.
        # It does NOT default to L4 because interactive use legitimately runs it.
        return RiskLevel.L3

    if risk == "medium":
        if requires_approval:
            # Approval-gated medium risk is L3 ONLY for external effects; local
            # checkpointed writes stay consequential-but-local at L2 (ADR: L3 is
            # "external effects", not "anything that asks").
            return (
                RiskLevel.L2
                if category in _LOCAL_CATEGORIES
                else RiskLevel.L3
            )
        return RiskLevel.L2

    # risk == "low"
    return RiskLevel.L2 if requires_approval else RiskLevel.L0


def enforce_level(level: RiskLevel, decision: Any) -> Any:
    """Slice 2 policy (mutates + returns `decision`):

    **L4 is never auto-allowed.** Whatever PermissionEngine said — standing rule,
    task grant, session allowlist — an irreversible/sensitive call is downgraded to
    an explicit human decision. Interactive approvals remain the only path through,
    every single time (there is no "always allow" for L4).

    L3 and below pass through unchanged in this slice; their standing-rule story
    stays with the existing §25 machinery.
    """
    if level >= RiskLevel.L4 and decision.allowed:
        decision.allowed = False
        decision.needs_user = True
        decision.rule = ""
        decision.reason = (
            f"irreversible action (L4) — explicit approval required"
            + (f"; was: {decision.reason}" if decision.reason else "")
        )
    return decision


# Path-shaped argument names a tool uses to declare its on-disk target, mirroring
# the PermissionEngine's write-scope list (a renamed argument must not bypass
# confinement by falling outside either check).
_PATH_ARGS = ("path", "file_path", "filepath", "file")


def declared_targets(arguments: Optional[dict[str, Any]]) -> list[str]:
    """The path-shaped target values this call actually carries."""
    out: list[str] = []
    for name in _PATH_ARGS:
        value = (arguments or {}).get(name)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def enforce_scope(
    decision: Any,
    arguments: Optional[dict[str, Any]],
    level: RiskLevel,
    *,
    workspace_root: Path,
    roots: list[tuple[Path, bool]],
) -> Any:
    """Slice 3 resource guard (mutates + returns `decision`):

    A side-effectful call (L1+) that declares an on-disk target must land inside
    the session's trusted roots — writable roots for real effect, read-only roots
    downgraded to ask. This holds regardless of which rule allowed the call: mode
    grants, session allowlists, standing rules and future grant paths all pass
    through here, so classifier drift or a new grant path cannot silently move a
    write outside the sandbox. Read-only calls (L0) are untouched — consulting
    files outside the workspace is legitimate (that is what directory grants are
    for); only side effects are confined.

    A violation never hard-denies interactively: the decision becomes an explicit
    human ask (which unattended runs resolve as deny+audit — fail closed).
    """
    if not getattr(decision, "allowed", False) or level < RiskLevel.L1:
        return decision

    targets = declared_targets(arguments)
    if not targets:
        return decision

    for target in targets:
        p = Path(target).expanduser()
        candidate = p.resolve() if p.is_absolute() else (workspace_root / p).resolve()
        under_any = False
        under_writable = False
        for root, writable in roots:
            try:
                candidate.relative_to(root)
                under_any = True
                under_writable = under_writable or writable
            except ValueError:
                continue
        if under_writable:
            continue
        detail = (
            f"target is in a read-only directory: {target}"
            if under_any
            else f"target is outside the trusted directories: {target}"
        )
        decision.allowed = False
        decision.needs_user = True
        decision.rule = ""
        decision.reason = (
            f"{detail} — explicit approval required"
            + (f"; was: {decision.reason}" if decision.reason else "")
        )
        return decision
    return decision


def restrict_grants(level: RiskLevel, decision: Any) -> Any:
    """Slice 4a grant gate (mutates + returns `decision`):

    **L3+ is never released by a blanket or approval-card grant.** Auto mode's "full
    access" and session-scoped ALWAYS_TOOL/ALWAYS_COMMAND entries minted by earlier
    approval cards are fine for local reversible work, but external effects (send
    message/file, shell-grade execution) need either an explicit per-action human
    decision or a user-authored policy artifact: a trusted-workspace command
    allowlist, a task-scoped standing rule, or configured auto-allow tools
    (decision.grant == "policy"). Unattended runs resolve the resulting ask through
    the same ApprovalService as interactive runs — fail closed when no one answers.

    L2 and below pass through unchanged; L4 was already forced to ask by slice 2.
    """
    if level < RiskLevel.L3 or not getattr(decision, "allowed", False):
        return decision
    if getattr(decision, "grant", "") == "policy":
        return decision
    decision.allowed = False
    decision.needs_user = True
    decision.rule = ""
    decision.reason = (
        "external effect (L3) requires explicit approval or standing policy"
        + (
            f"; was auto-allowed by {getattr(decision, 'grant', '')} grant"
            if getattr(decision, "grant", "")
            else ""
        )
        + (f"; was: {decision.reason}" if decision.reason else "")
    )
    return decision


def isolation_status(level: Any) -> str:
    """Honest sandbox declaration for audit rows (ARCH-002: users are told the
    truth about consequences). Nothing executes in a container today; L1 writes
    are covered by session checkpoints, everything above that runs unsandboxed."""
    if level is None:
        return ""
    if level < RiskLevel.L1:
        return "read-only"
    if level == RiskLevel.L1:
        return "checkpoint"
    return "none"
