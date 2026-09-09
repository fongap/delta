"""Execution Gateway — Rust-authoritative policy evaluation (ADR-030).

Rust ``delta_core`` is the sole authority for tool call classification
and policy enforcement (the four slices: classify, enforce_level,
restrict_grants, enforce_scope). Python retains only:

- The ``RiskLevel`` enum (for type annotations and audit)
- ``write_paths`` (a path-extraction utility used by PermissionEngine)
- ``isolation_status`` (a display helper for audit rows)
- Thin facades that delegate to the Rust authority via DeltaCoreClient

Fail-closed: if the Rust authority is unavailable, classification
returns L4 (never auto-allowed) and evaluation denies with needs_user.

Contract: ``docs/architecture/adr/ADR-030-r2-policy-hard-cut.md``
"""

from __future__ import annotations

import re
from enum import IntEnum
from pathlib import Path
from typing import Any

from packages.delta_core_client import DeltaCoreError, default_client


class RiskLevel(IntEnum):
    L0 = 0  # read-only, no side effects
    L1 = 1  # reversible local writes (checkpointed)
    L2 = 2  # consequential local writes / config changes
    L3 = 3  # external effects, compensatable
    L4 = 4  # irreversible or sensitive — never auto-allowed

    @property
    def label(self) -> str:
        return self.name


# -- Utility functions (not policy authority) -----------------------------------

# Path-shaped argument names a tool uses to declare its on-disk target.
_PATH_ARGS = ("path", "file_path", "filepath", "file")

# The blob argument each patch-style write tool carries its targets in.
_PATCH_BLOB_ARG = {"apply_patch": "patch", "apply_unified_diff": "diff"}

# apply_patch (Codex format) file headers — including the rename target — and
# the `+++ b/<path>` headers of unified diffs.
_APPLY_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_APPLY_PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)
_UNIFIED_DIFF_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+?)\s*$", re.MULTILINE)


def write_paths(tool_name: str, arguments: dict[str, Any] | None) -> tuple[list[str], bool]:
    """Every filesystem path a write tool would touch, for root scoping/confinement.

    Returns ``(paths, located)``. ``located`` is False when the path can't be determined
    (a write tool with no declared path argument and no parseable blob header) — the
    caller must then fail closed to an explicit human decision rather than skip scoping,
    so an unscoped write can't slip through auto/custom mode.
    """
    arguments = arguments or {}
    for arg in _PATH_ARGS:
        value = arguments.get(arg)
        if isinstance(value, str) and value.strip():
            return [value.strip()], True
    blob_arg = _PATCH_BLOB_ARG.get(tool_name)
    if blob_arg is not None:
        blob = str(arguments.get(blob_arg, ""))
        if tool_name == "apply_patch":
            paths = _APPLY_PATCH_FILE.findall(blob) + _APPLY_PATCH_MOVE.findall(blob)
        else:  # apply_unified_diff
            paths = [
                p for p in _UNIFIED_DIFF_FILE.findall(blob) if p and p != "/dev/null"
            ]
        return [p.strip() for p in paths], bool(paths)
    # Unknown write tool (e.g. one promoted to write via a user override): we cannot
    # locate its target, so it cannot be auto-scoped.
    return [], False


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


# -- Rust-authoritative policy facade ------------------------------------------

class PolicyAuthorityError(RuntimeError):
    """Raised when the policy authority (Rust delta_core) fails."""


def _metadata_to_dict(metadata: Any) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        "risk_level": str(getattr(metadata, "risk_level", "") or ""),
        "requires_approval": bool(getattr(metadata, "requires_approval", False)),
        "category": str(getattr(metadata, "category", "") or ""),
        "capabilities": list(getattr(metadata, "capabilities", []) or []),
    }


def _decision_to_dict(decision: Any) -> dict[str, Any]:
    return {
        "allowed": bool(getattr(decision, "allowed", False)),
        "reason": str(getattr(decision, "reason", "") or ""),
        "needs_user": bool(getattr(decision, "needs_user", False)),
        "rule": str(getattr(decision, "rule", "") or ""),
        "grant": str(getattr(decision, "grant", "") or ""),
    }


def _dict_to_decision(d: dict[str, Any]) -> Any:
    from core.permissions import Decision

    return Decision(
        allowed=bool(d.get("allowed", False)),
        reason=str(d.get("reason", "") or ""),
        needs_user=bool(d.get("needs_user", False)),
        rule=str(d.get("rule", "") or ""),
        grant=str(d.get("grant", "") or ""),
    )


def classify(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    metadata: Any = None,
) -> RiskLevel:
    """Delegate to Rust ``policy.classify``. Fail-closed: L4 on any error."""
    try:
        client = default_client()
        result = client.command(
            {
                "cmd": "policy.classify",
                "tool_name": tool_name,
                "arguments": arguments,
                "metadata": _metadata_to_dict(metadata),
            }
        )
        return RiskLevel(int(result.get("level", 4)))
    except (DeltaCoreError, KeyError, TypeError, ValueError):
        return RiskLevel.L4


def evaluate_policy(
    decision: Any,
    level: int,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    metadata: Any = None,
    *,
    workspace_root: Path,
    roots: list[tuple[Path, bool]],
) -> tuple[Any, RiskLevel]:
    """Delegate to Rust ``policy.evaluate`` (all four slices in one call).

    Returns ``(Decision, RiskLevel)``. Fail-closed: on error, returns a
    denied decision with L4.
    """
    try:
        client = default_client()
        result = client.command(
            {
                "cmd": "policy.evaluate",
                "tool_name": tool_name,
                "arguments": arguments,
                "metadata": _metadata_to_dict(metadata),
                "decision": _decision_to_dict(decision),
                "level": int(level),
                "workspace_root": str(workspace_root),
                "roots": [{"path": str(p), "writable": w} for p, w in roots],
            }
        )
        out_decision = _dict_to_decision(result["decision"])
        out_level = RiskLevel(int(result.get("level", 4)))
        return out_decision, out_level
    except (DeltaCoreError, KeyError, TypeError, ValueError):
        from core.permissions import Decision

        return (
            Decision(
                allowed=False,
                reason="policy authority unavailable — explicit approval required",
                needs_user=True,
            ),
            RiskLevel.L4,
        )
