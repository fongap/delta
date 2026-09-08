"""Rust Core delegate for the Source/Citation final validity verdict.

When ``DELTA_RUST_AUTHORITY=source_citation`` is declared, callers send
the compatibility SourceRef snapshot and candidate range to the unified
``delta_core`` process.  Rust owns the final verdict; protocol or process
failures propagate and must never trigger a Python verdict fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.delta_core_client import DeltaCoreError, default_client


_REQUIRED_RESULT_FIELDS = frozenset(
    {
        "validity",
        "valid",
        "status",
        "reason",
        "source_exists",
        "source_unchanged",
        "structure_valid",
        "revision_matches",
        "range_valid",
    }
)


def validate_citation_delegated(
    source: dict[str, Any] | None,
    range_obj: dict[str, Any],
    *,
    workspace: str | Path | None,
) -> dict[str, Any]:
    """Return the typed Rust verdict, rejecting malformed responses."""
    result = default_client().command(
        {
            "cmd": "citation.validate",
            "source": source,
            "range": range_obj,
            "workspace": str(workspace) if workspace is not None else None,
        }
    )
    if not isinstance(result, dict):
        raise DeltaCoreError(
            "citation.validate returned a non-object result; refusing Python fallback"
        )
    missing = sorted(_REQUIRED_RESULT_FIELDS - result.keys())
    if missing:
        raise DeltaCoreError(
            "citation.validate response missing required fields: "
            + ", ".join(missing)
        )
    return result


def canonicalize_citations_delegated(
    ranges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Canonicalize candidate ranges in Rust before store mutation."""
    result = default_client().command(
        {"cmd": "citation.canonicalize", "ranges": ranges}
    )
    if not isinstance(result, list) or not all(
        isinstance(item, dict) for item in result
    ):
        raise DeltaCoreError(
            "citation.canonicalize returned a malformed result; "
            "refusing Python fallback"
        )
    return result


__all__ = ["canonicalize_citations_delegated", "validate_citation_delegated"]
