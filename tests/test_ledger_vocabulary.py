"""Tests for ADR-005 WS1: Run Event Ledger vocabulary expansion.

The ledger gains a fixed event vocabulary (`KNOWN_EVENT_TYPES`) and the
manager's audit sink is mirrored into the ledger when an ambient run scope
names the owning run. AuditStore stays as a backward-compatible view; these
tests cover the new ledger-only facts.
"""

from __future__ import annotations


import pytest

from core.ledger import KNOWN_EVENT_TYPES, RunEventLedger
from core.ledger_event import make_mirroring_audit_sink
from core import runscope


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run-events.db")
    yield inst
    inst.close()


def test_known_event_types_include_artifact_validation_and_approval():
    """The vocabulary ADR-005 introduces is exposed and complete."""
    for required in (
        "tool.proposed",
        "tool.started",
        "tool.finished",
        "tool.denied",
        "approval.requested",
        "approval.granted",
        "approval.denied",
        "artifact.registered",
        "artifact.completed",
        "validation.started",
        "validation.passed",
        "validation.failed",
        "side_effect.committed",
        "side_effect.replayed",
        "side_effect.uncommitted",
        "run.resumed",
    ):
        assert required in KNOWN_EVENT_TYPES


def test_ledger_appends_unknown_event_type_without_validation_error(ledger):
    """The vocabulary is documentation, not a strict allowlist — engine code
    may still emit ad-hoc `tool.<stage>` events without breaking the chain."""
    row = ledger.append("run-1", "tool.custom_thing", actor="tool", payload={"x": 1})
    assert row["type"] == "tool.custom_thing"
    assert row["hash"]


def test_mirroring_sink_writes_audit_and_ledger_inside_run_scope(ledger):
    """Inside a driven run, the audit row becomes both an audit row AND a
    ledger event. The stage value maps to a known ledger type."""
    audit_rows: list[dict] = []

    def audit_sink(event: dict) -> None:
        audit_rows.append(event)

    sink = make_mirroring_audit_sink(
        audit_sink, ledger_append=ledger.append
    )
    token = runscope.set_current("run-abc", "sess-1")
    try:
        sink(
            {
                "tool": "write_file",
                "stage": "finished",
                "status": "ok",
                "level": "L2",
                "isolation": "checkpoint",
                "arguments": {"path": "/x.md", "text": "hi"},
                "reason": "",
            }
        )
    finally:
        runscope.reset(token)

    assert len(audit_rows) == 1
    events = ledger.events("run-abc")
    assert len(events) == 1
    assert events[0]["type"] == "tool.finished"
    assert events[0]["actor"] == "tool"
    assert events[0]["payload"]["tool"] == "write_file"
    assert events[0]["payload"]["session_id"] == "sess-1"
    # Sanitizer kept the path in the payload; the in-memory text wasn't
    # recorded by audit, so it isn't in the ledger either.
    assert events[0]["payload"]["arguments"]["path"] == "/x.md"


def test_mirroring_sink_outside_run_scope_only_writes_audit(ledger):
    """Background teardown / pre-bind bootstrap have no run to attribute to:
    the audit row is preserved but the ledger is not touched."""
    audit_rows: list[dict] = []

    def audit_sink(event: dict) -> None:
        audit_rows.append(event)

    sink = make_mirroring_audit_sink(
        audit_sink, ledger_append=ledger.append
    )
    # No runscope token — manager background teardown.
    sink({"tool": "run_shell", "stage": "teardown", "status": "killed"})
    assert len(audit_rows) == 1
    assert ledger.runs() == []


def test_mirroring_sink_approval_stage_maps_to_approval_event(ledger):
    """Approval events ride the same audit pipeline but land in the ledger as
    `approval.*` so the run narrative distinguishes them from `tool.*`."""
    audit_rows: list[dict] = []

    def audit_sink(event: dict) -> None:
        audit_rows.append(event)

    sink = make_mirroring_audit_sink(
        audit_sink, ledger_append=ledger.append
    )
    token = runscope.set_current("run-xyz")
    try:
        sink(
            {
                "tool": "send_email",
                "stage": "approval_requested",
                "status": "ask",
                "reason": "external effect",
            }
        )
        sink(
            {
                "tool": "send_email",
                "stage": "approval_granted",
                "status": "ok",
                "reason": "user approved",
            }
        )
    finally:
        runscope.reset(token)

    types = [e["type"] for e in ledger.events("run-xyz")]
    assert types == ["approval.requested", "approval.granted"]


def test_mirroring_sink_unknown_stage_becomes_tool_stage(ledger):
    """Custom stages don't get lost; they're recorded as `tool.<stage>` so the
    chain remains replayable even for stages the vocabulary hasn't named yet."""
    audit_rows: list[dict] = []

    def audit_sink(event: dict) -> None:
        audit_rows.append(event)

    sink = make_mirroring_audit_sink(
        audit_sink, ledger_append=ledger.append
    )
    token = runscope.set_current("run-zzz")
    try:
        sink({"tool": "shell_x", "stage": "preflight", "status": "ok"})
    finally:
        runscope.reset(token)
    events = ledger.events("run-zzz")
    assert events[0]["type"] == "tool.preflight"


def test_mirroring_sink_swallows_ledger_failures():
    """A locked ledger DB or any failure in the mirror path must not break the
    primary audit path. The audit row is the source of truth; the mirror is
    best-effort."""
    audit_rows: list[dict] = []

    def audit_sink(event: dict) -> None:
        audit_rows.append(event)

    def bad_append(run_id: str, type: str, payload):
        raise RuntimeError("ledger locked")

    sink = make_mirroring_audit_sink(
        audit_sink, ledger_append=bad_append
    )
    token = runscope.set_current("run-bad")
    try:
        sink({"tool": "write_file", "stage": "finished", "status": "ok"})
    finally:
        runscope.reset(token)
    assert len(audit_rows) == 1
