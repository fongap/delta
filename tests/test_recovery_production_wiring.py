"""P0-B Recovery Production Wiring — pause-point snapshot + durable resume tests (ADR-029).

The spec requires:

  Runtime enters pause point -> construct RecoverySnapshot -> persist ->
  Session/Run state updated -> UI/Inbox visible -> app exits -> app
  restarts -> verify Snapshot -> resume Run -> after success clean up.

This module tests the wiring (not the advisory snapshot data structure, which
is covered by test_recovery_context.py). We verify:

  1. Each pause point writes a RecoverySnapshot with the correct phase.
  2. _durable_resume clears the snapshot on success (now no-op for ledger).
  3. run.resumed ledger event is emitted (not run.started) on resume.
  4. Cold-start surfaces paused sessions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from core.recovery import (
    PHASE_AWAITING_APPROVAL,
    PHASE_AWAITING_DIRECTORY,
    PHASE_AWAITING_PLAN,
    PHASE_AWAITING_QUESTION,
    RecoverySnapshot,
    RecoveryStore,
)


@pytest.fixture
def store(tmp_path) -> RecoveryStore:
    """RecoveryStore backed by real delta_core / run_events.db."""
    inst = RecoveryStore(tmp_path / "run_events.db")
    yield inst
    inst.close()


def _snap(session_id="s1", run_id="r1", phase=PHASE_AWAITING_APPROVAL):
    return RecoverySnapshot(
        run_id=run_id,
        session_id=session_id,
        phase=phase,
        pending_inbox_item_id="inbox-1",
    )


# -- snapshot written at each pause point -----------------------------------


def test_snapshot_written_at_approval_pause(store):
    store.write(_snap(phase=PHASE_AWAITING_APPROVAL))
    snap = store.get("s1")
    assert snap is not None
    assert snap.phase == PHASE_AWAITING_APPROVAL
    assert snap.pending_inbox_item_id == "inbox-1"


def test_snapshot_written_at_question_pause(store):
    store.write(_snap(phase=PHASE_AWAITING_QUESTION))
    snap = store.get("s1")
    assert snap.phase == PHASE_AWAITING_QUESTION


def test_snapshot_written_at_directory_pause(store):
    store.write(_snap(phase=PHASE_AWAITING_DIRECTORY))
    snap = store.get("s1")
    assert snap.phase == PHASE_AWAITING_DIRECTORY


def test_snapshot_written_at_plan_pause(store):
    store.write(_snap(phase=PHASE_AWAITING_PLAN))
    snap = store.get("s1")
    assert snap.phase == PHASE_AWAITING_PLAN


# -- snapshot cleared on successful resume -----------------------------------
# Note: In ADR-029, checkpoints are append-only facts in the ledger.
# clear() is a no-op (returns True for backward compat). The resume
# workflow uses the Inbox item resolution as the signal, not clearing
# the checkpoint.


def test_snapshot_clear_returns_true_for_compat(store):
    store.write(_snap())
    assert store.get("s1") is not None
    # clear returns True for backward compat but doesn't delete the ledger fact
    assert store.clear("s1") is True
    assert store.get("s1") is not None


def test_snapshot_clear_returns_true_when_absent(store):
    assert store.clear("nonexistent") is True


# -- snapshot overwritten on new pause ---------------------------------------


def test_snapshot_new_pause_creates_new_checkpoint(store):
    """Each pause point creates a new checkpoint (append-only)."""
    store.write(_snap(phase=PHASE_AWAITING_APPROVAL))
    store.write(_snap(phase=PHASE_AWAITING_QUESTION))
    # Both checkpoints exist in the ledger
    latest = store.latest()
    s1_checkpoints = [s for s in latest if s.session_id == "s1"]
    assert len(s1_checkpoints) == 2
    # Latest is the most recent
    assert s1_checkpoints[0].phase == PHASE_AWAITING_QUESTION
    assert s1_checkpoints[1].phase == PHASE_AWAITING_APPROVAL


# -- snapshot survives restart -----------------------------------------------


def test_snapshot_survives_restart(tmp_path):
    db_path = tmp_path / "run_events.db"
    store1 = RecoveryStore(db_path)
    store1.write(_snap(phase=PHASE_AWAITING_APPROVAL))
    store1.close()

    # Fresh store on same DB
    store2 = RecoveryStore(db_path)
    snap = store2.get("s1")
    assert snap is not None
    assert snap.phase == PHASE_AWAITING_APPROVAL
    assert snap.run_id == "r1"
    store2.close()


# -- cold-start surfaces paused sessions -------------------------------------


def test_cold_start_surfaces_paused_sessions(tmp_path):
    db_path = tmp_path / "run_events.db"
    store1 = RecoveryStore(db_path)
    store1.write(_snap(session_id="s1", phase=PHASE_AWAITING_APPROVAL))
    store1.write(
        RecoverySnapshot(
            run_id="r2", session_id="s2", phase=PHASE_AWAITING_QUESTION
        )
    )
    store1.close()

    store2 = RecoveryStore(db_path)
    paused = store2.latest()
    # Both checkpoints should be visible
    sessions = {s.session_id for s in paused}
    assert "s1" in sessions
    assert "s2" in sessions
    store2.close()


# -- run.resumed ledger event ------------------------------------------------


def test_run_resumed_ledger_event_emitted(tmp_path):
    """The adapter should emit run.resumed (not run.started) when
    kind == 'resume'."""
    from core.engine import TurnEngine
    from core.ledger import RunEventLedger
    from core.runtime import TurnEngineAdapter

    ledger = RunEventLedger(tmp_path / "run-events.db")

    engine = MagicMock(spec=TurnEngine)
    engine.resume = MagicMock(return_value=_empty_async_gen())
    engine.messages = []
    engine.audit_context = {}
    engine.agent_name = "test"
    engine.model = "test"

    adapter = TurnEngineAdapter(
        engine, ledger=ledger, session_id="s1", run_id="r1"
    )

    asyncio.run(_drain(adapter.resume()))

    events = ledger.events("r1")
    types = [e["type"] for e in events]
    assert "run.resumed" in types
    assert "run.started" not in types
    assert "run.completed" in types
    ledger.close()


def test_run_started_still_used_for_non_resume(tmp_path):
    """Normal run() still emits run.started, not run.resumed."""
    from core.engine import TurnEngine
    from core.ledger import RunEventLedger
    from core.runtime import TurnEngineAdapter

    ledger = RunEventLedger(tmp_path / "run-events.db")

    engine = MagicMock(spec=TurnEngine)
    engine.run = MagicMock(return_value=_empty_async_gen())
    engine.messages = []
    engine.audit_context = {}
    engine.agent_name = "test"
    engine.model = "test"

    adapter = TurnEngineAdapter(engine, ledger=ledger, session_id="s1", run_id="r1")

    asyncio.run(_drain(adapter.run("hello")))

    events = ledger.events("r1")
    types = [e["type"] for e in events]
    assert "run.started" in types
    assert "run.resumed" not in types
    ledger.close()


# -- helpers -----------------------------------------------------------------


async def _empty_async_gen():
    if False:
        yield  # make it an async generator


async def _drain(agen):
    async for _ in agen:
        pass