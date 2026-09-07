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


# -- P0-4: derive_run_status (ledger is single source of truth) ---------------


def test_derive_run_status_terminal_state_overrides_cached(ledger):
    """P0-4: the ledger's terminal status always wins over the cached
    ``TaskRun.status`` column. A stale ``run.status = 'running'`` must
    not survive a ledger ``run.completed`` event."""
    ledger.append("r1", "run.started")
    ledger.append("r1", "run.completed")
    # Stale cached value: the run is actually done.
    assert ledger.derive_run_status("r1", fallback="running") == "ok"


def test_derive_run_status_fallback_for_never_started_runs(ledger):
    """A run with no ledger events (e.g. a skipped task) falls back to
    the cached ``TaskRun.status`` so we can still surface 'skipped'."""
    assert ledger.derive_run_status("never-run", fallback="skipped") == "skipped"


def test_derive_run_status_no_fallback_returns_unknown(ledger):
    """No events and no fallback → 'unknown' (the run was never seen)."""
    assert ledger.derive_run_status("never-seen", fallback=None) == "unknown"


def test_derive_run_status_covers_all_required_states(ledger):
    """R1.5 contract: running, ok, error, validation_failed, interrupted,
    unknown are all derived from the ledger (no second copy of terminal
    facts in TaskRun.status)."""
    # running
    ledger.append("r-run", "run.started")
    assert ledger.derive_run_status("r-run") == "running"
    # ok
    ledger.append("r-ok", "run.started")
    ledger.append("r-ok", "run.completed")
    assert ledger.derive_run_status("r-ok") == "ok"
    # error
    ledger.append("r-err", "run.started")
    ledger.append("r-err", "run.failed", payload={"reason": "boom"})
    assert ledger.derive_run_status("r-err") == "error"
    # validation_failed
    ledger.append("r-val", "run.started")
    ledger.append("r-val", "validation.failed", payload={"reason": "missing"})
    assert ledger.derive_run_status("r-val") == "validation_failed"
    # interrupted (via recover_stale)
    ledger.append("r-int", "run.started")
    ledger.recover_stale()
    assert ledger.derive_run_status("r-int") == "interrupted"
    # unknown
    assert ledger.derive_run_status("r-unknown") == "unknown"
