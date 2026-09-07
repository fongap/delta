"""Run state authority tests (P1-C).

The ledger is the single source of truth for run lifecycle. The
``RunEventLedger.run_status(run_id)`` method derives the UI-facing
``status`` from the ledger's events. These tests verify the mapping.
"""

from __future__ import annotations

import pytest

from core.ledger import RunEventLedger


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run_events.db")
    yield inst
    inst.close()


def test_status_unknown_for_no_events(ledger):
    assert ledger.run_status("run-with-no-events") == "unknown"


def test_status_running_after_start(ledger):
    ledger.append("r1", "run.started", payload={"kind": "run"})
    assert ledger.run_status("r1") == "running"


def test_status_resumed_after_resume(ledger):
    ledger.append("r1", "run.started", payload={"kind": "run"})
    ledger.append("r1", "run.resumed", payload={"kind": "resume"})
    assert ledger.run_status("r1") == "resumed"


def test_status_ok_after_completed(ledger):
    ledger.append("r1", "run.started", payload={"kind": "run"})
    ledger.append("r1", "tool.finished", payload={"name": "read_file"})
    ledger.append("r1", "run.completed", payload={"kind": "run"})
    assert ledger.run_status("r1") == "ok"


def test_status_error_after_failed(ledger):
    ledger.append("r1", "run.started", payload={"kind": "run"})
    ledger.append("r1", "run.failed", payload={"reason": "boom"})
    assert ledger.run_status("r1") == "error"


def test_status_interrupted_after_crash_recovery(ledger):
    """Cold-start recovery writes run.interrupted for any open run."""
    ledger.append("r1", "run.started", payload={"kind": "run"})
    ledger.append("r1", "tool.started", payload={"tool": "shell"})
    ledger.recover_stale()
    assert ledger.run_status("r1") == "interrupted"


def test_status_validation_failed_when_validation_event_is_last(ledger):
    """A run that ended with validation.failed (no run.completed) is
    validation_failed. This bridges the gap where the engine writes
    run.completed unconditionally but validation verdict comes after."""
    ledger.append("r1", "run.started")
    ledger.append("r1", "validation.failed", payload={"reason": "missing artifact"})
    assert ledger.run_status("r1") == "validation_failed"


def test_status_uses_last_terminal_event(ledger):
    """If somehow both run.completed and run.failed are present (should
    not happen in production), the latest by seq wins."""
    ledger.append("r1", "run.started")
    ledger.append("r1", "run.completed")
    ledger.append("r1", "run.failed")
    assert ledger.run_status("r1") == "error"


def test_status_empty_run_id_returns_unknown(ledger):
    assert ledger.run_status("") == "unknown"


def test_status_includes_run_with_only_tool_events(ledger):
    """A run with only tool.finished (no run.started event recorded) is
    classified by its latest event."""
    ledger.append("r1", "tool.finished", payload={"name": "read_file"})
    assert ledger.run_status("r1") == "unknown"
