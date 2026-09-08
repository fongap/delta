"""Fail-closed Rust Core delegate for deterministic Validation."""

from __future__ import annotations

from typing import Any

from packages.delta_core_client import DeltaCoreError, default_client


def run_validation_delegated(
    artifacts: list[dict[str, Any]],
    criteria: dict[str, Any],
    *,
    workspace: str | None,
    valid_citation_count: int | None,
) -> dict[str, Any]:
    """Run Validation in Rust and validate the response envelope."""
    result = default_client().command(
        {
            "cmd": "validation.run",
            "artifacts": artifacts,
            "criteria": criteria,
            "workspace": workspace,
            "valid_citation_count": valid_citation_count,
        }
    )
    if not isinstance(result, dict):
        raise DeltaCoreError(
            "validation.run returned a non-object result; refusing Python fallback"
        )
    if not isinstance(result.get("ok"), bool):
        raise DeltaCoreError("validation.run response missing boolean ok")
    if not isinstance(result.get("checks"), list):
        raise DeltaCoreError("validation.run response missing checks list")
    if not isinstance(result.get("evidence"), dict):
        raise DeltaCoreError("validation.run response missing evidence object")
    return result


__all__ = ["run_validation_delegated"]
