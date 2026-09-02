"""Mirror the `audit_sink` payload into the Run Event Ledger.

`core/engine.py:_audit` writes one dict per tool call to the audit sink. The
audit sink is owned by the application layer (`services/server/manager.py`),
which decides where facts land. ADR-005 (Reliable Task Runtime) collapses
tool/approval/artifact facts into the run ledger so a single run is replayable
from one source; AuditStore stays as a backward-compatible view of the same
events for old callers.

This module exposes one helper, `mirror_audit_to_ledger`, which the manager
threads through `audit_sink` so every audit row also becomes a ledger event.
Inside a driven run the ambient `runscope` names the owning run_id; outside
any run (background teardown, pre-bind bootstrap) the audit row has no
ledger run to attribute to and is dropped from the ledger — the AuditStore
still records it for completeness.
"""

from __future__ import annotations

from typing import Any, Callable

from core import runscope

# The shape of the ledger's `append` method this helper needs. Kept as a local
# alias so the engine/manager can inject any compatible callable (tests pass
# a lambda; production passes `RunEventLedger.append`, whose dict return the
# helper ignores).
LedgerAppend = Callable[..., object]


# Map audit `stage` values to ledger event types. Anything not in this map is
# still recorded as `tool.<stage>` so we never silently drop information; the
# ledger vocabulary is open. Keep explicit names for stages that downstream
# tooling already special-cases.
_STAGE_TO_LEDGER_TYPE: dict[str, str] = {
    "proposed": "tool.proposed",
    "started": "tool.started",
    "finished": "tool.finished",
    "denied": "tool.denied",
    "approval_requested": "approval.requested",
    # engine emits `approval_resolved {status: approved|denied}` after the
    # user decides; we map it to the canonical approval.* pair by status.
    "approval_granted": "approval.granted",
    "approval_denied": "approval.denied",
    "standing_rule_minted": "approval.granted",
}


def _resolve_approval(stage: str, status: str | None) -> str | None:
    """Return the canonical approval.* type for a resolved approval, or None
    if this stage isn't a resolved approval event."""
    if stage == "approval_resolved":
        if status == "approved":
            return "approval.granted"
        if status == "denied":
            return "approval.denied"
    return None


def _ledger_type(stage: str) -> str:
    return _STAGE_TO_LEDGER_TYPE.get(stage, f"tool.{stage}")


def make_mirroring_audit_sink(
    audit_sink: Callable[[dict[str, Any]], None],
    *,
    ledger_append: LedgerAppend | None = None,
    actor: str = "tool",
) -> Callable[[dict[str, Any]], None]:
    """Return an audit sink that writes to both the AuditStore and the run ledger.

    Pass `ledger_append=ledger.append` to enable mirroring. The helper calls it
    with `(run_id, type, payload=..., actor=...)`, matching
    `RunEventLedger.append`'s keyword-only tail — any compatible callable works
    (tests pass a lambda; production passes the ledger method).

    Failures in either sink are swallowed independently: the audit sink is the
    primary path and ledger mirroring is best-effort (ledger DB may be locked
    or the run may have been reaped). The audit row already records the fact;
    dropping the mirror does not lose data.
    """

    def sink(event: dict[str, Any]) -> None:
        try:
            audit_sink(event)
        except Exception:
            pass
        if ledger_append is None:
            return
        stage = str(event.get("stage") or "")
        if not stage:
            return
        scope = runscope.current()
        if scope is None:
            return
        run_id, session_id = scope
        if not run_id:
            return
        event_type = _resolve_approval(stage, event.get("status")) or _ledger_type(stage)
        payload = {
            "tool": event.get("tool") or event.get("tool_name") or "",
            "stage": stage,
            "status": event.get("status", ""),
            "reason": event.get("reason", ""),
            "level": event.get("level", ""),
            "isolation": event.get("isolation", ""),
            "session_id": session_id,
        }
        if event.get("arguments") is not None:
            payload["arguments"] = event.get("arguments")
        if event.get("result_preview") is not None:
            payload["result_preview"] = event.get("result_preview")
        if event.get("resource") is not None:
            payload["resource"] = event.get("resource")
        try:
            ledger_append(run_id, event_type, payload=payload, actor=actor)
        except Exception:
            pass

    return sink
