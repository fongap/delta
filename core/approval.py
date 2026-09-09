"""Approval Authority facade — Rust-authoritative audit persistence (ADR-031).

Rust ``delta_core`` is the sole authority for writing approval audit events
to the ``audit_events`` table. The interactive approval decision itself
(``ApprovalOutcome``, ``PermissionRequest``, ``Approver`` callback) remains
in Python ``engine.py``.

Only the audit *write* path is delegated here; the *read* path (``list``)
stays in ``core/audit.py`` via its own SQLite connection to the same
database file.

Fail-closed: if the Rust authority is unavailable, the audit event is not
written (callers already swallow ``Exception`` in audit sinks).

Contract: ``docs/architecture/adr/ADR-031-r2-approval-hard-cut.md``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.delta_core_client import DeltaCoreError, default_client


class ApprovalAuthorityError(RuntimeError):
    """Raised when the approval audit authority (Rust delta_core) fails."""


def record(
    db_path: str | Path,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Delegate to Rust ``approval.record``.

    Returns ``{"id": int, "timestamp": str}`` on success.

    Raises :class:`ApprovalAuthorityError` on any failure.
    """
    try:
        client = default_client()
        result = client.command(
            {
                "cmd": "approval.record",
                "db": str(db_path),
                "session_id": str(event.get("session_id") or ""),
                "agent": event.get("agent"),
                "workspace": event.get("workspace"),
                "connector": event.get("connector"),
                "tool": str(event.get("tool") or event.get("tool_name") or ""),
                "stage": str(event.get("stage") or ""),
                "status": event.get("status"),
                "approval": event.get("approval"),
                "arguments": event.get("arguments"),
                "result_preview": event.get("result_preview"),
                "reason": event.get("reason"),
                "resource": event.get("resource"),
                "level": event.get("level"),
                "isolation": event.get("isolation"),
            }
        )
        return {"id": int(result["id"]), "timestamp": str(result["timestamp"])}
    except (DeltaCoreError, KeyError, TypeError, ValueError) as exc:
        raise ApprovalAuthorityError(str(exc)) from exc
