"""Run lifecycle transition authority tests (ADR-042).

The ``run.transition`` command validates run lifecycle state-machine
transitions before appending.  Legal transitions succeed and produce
ledger events; illegal transitions are rejected with ``DeltaCoreError``.

These tests verify the Rust-side state machine enforcement.
"""

from __future__ import annotations

import pytest

from packages.delta_core_client import DeltaCoreError
from core.ledger import RunEventLedger


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run_events.db")
    yield inst
    inst.close()


# -- Legal transitions --------------------------------------------------------


def test_transition_started_from_unknown(ledger):
    row = ledger.transition("r1", "run.started", actor="user", payload={"kind": "run"})
    assert row["type"] == "run.started"
    assert row["seq"] == 1
    assert ledger.run_status("r1") == "running"


def test_transition_completed_from_running(ledger):
    ledger.transition("r1", "run.started", actor="user")
    row = ledger.transition("r1", "run.completed", actor="system")
    assert row["type"] == "run.completed"
    assert row["seq"] == 2
    assert ledger.run_status("r1") == "ok"


def test_transition_failed_from_running(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.failed", actor="system", payload={"reason": "boom"})
    assert ledger.run_status("r1") == "error"


def test_transition_resumed_from_running(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.resumed", actor="system")
    assert ledger.run_status("r1") == "resumed"


def test_transition_completed_from_resumed(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.resumed", actor="system")
    ledger.transition("r1", "run.completed", actor="system")
    assert ledger.run_status("r1") == "ok"


def test_transition_failed_from_resumed(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.resumed", actor="system")
    ledger.transition("r1", "run.failed", actor="system", payload={"reason": "boom"})
    assert ledger.run_status("r1") == "error"


def test_transition_interrupted_from_running(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.interrupted", actor="system", payload={"reason": "crashed"})
    assert ledger.run_status("r1") == "interrupted"


# -- Illegal transitions ------------------------------------------------------


def test_transition_rejects_started_from_running(ledger):
    ledger.transition("r1", "run.started", actor="user")
    with pytest.raises(DeltaCoreError, match="illegal run transition"):
        ledger.transition("r1", "run.started", actor="user")


def test_transition_rejects_completed_from_terminal(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.completed", actor="system")
    with pytest.raises(DeltaCoreError, match="illegal run transition"):
        ledger.transition("r1", "run.completed", actor="system")


def test_transition_rejects_resumed_from_unknown(ledger):
    with pytest.raises(DeltaCoreError, match="illegal run transition"):
        ledger.transition("r1", "run.resumed", actor="system")


def test_transition_rejects_started_after_completed(ledger):
    ledger.transition("r1", "run.started", actor="user")
    ledger.transition("r1", "run.completed", actor="system")
    with pytest.raises(DeltaCoreError, match="illegal run transition"):
        ledger.transition("r1", "run.started", actor="user")


def test_transition_rejects_non_run_event(ledger):
    with pytest.raises(DeltaCoreError, match="requires a run"):
        ledger.transition("r1", "tool.started", actor="user")


# -- Hash chain integrity ------------------------------------------------------


def test_transition_extends_hash_chain(ledger):
    ledger.transition("r1", "run.started", actor="user", ts=1000.0)
    row = ledger.transition("r1", "run.completed", actor="system", ts=1001.0)
    assert row["prev_hash"] != ""
    assert ledger.verify("r1")


# -- Non-run events still use append ------------------------------------------


def test_append_still_works_for_non_run_events(ledger):
    ledger.append("r1", "run.started", actor="user")
    ledger.append("r1", "tool.started", payload={"tool": "read_file"})
    ledger.append("r1", "tool.finished", payload={"status": "ok"})
    ledger.append("r1", "run.completed", actor="system")
    assert ledger.run_status("r1") == "ok"
