"""P0-A Side Effect Crash Safety — crash-point tests.

The spec requires testing six crash points in the side-effect lifecycle:

  1. Intent written before crash          (Planned row exists, no execute)
  2. Crash after intent, before execute   (Planned row, no commit)
  3. Crash during external execution      (Executing row, no commit)
  4. Crash after external success,        (no commit row yet)
     before commit
  5. Crash after commit, before tool      (Committed row; resume replays)
     result written to messages
  6. Crash after tool result written      (Committed row; resume replays)

Acceptance:
  * Committed side effects are NOT re-executed.
  * Uncertain side effects are NOT auto-replayed.
  * User can resolve an Uncertain side effect (confirm / re-execute / dismiss).
  * Resume state is explainable.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from core.idemlog import IdempotencyLog, SideEffectState, args_sha256, operation_id
from core.ledger import RunEventLedger


@pytest.fixture
def idem(tmp_path):
    inst = IdempotencyLog(tmp_path / "side_effects.db")
    yield inst
    inst.close()


@pytest.fixture
def ledger(tmp_path):
    inst = RunEventLedger(tmp_path / "run-events.db")
    yield inst
    inst.close()


# -- helpers ----------------------------------------------------------------

RUN_ID = "run-crash-test"
CALL_ID = "call-write-1"
TOOL = "write_file"
ARGS = {"path": "report.md", "content": "# Hello\n"}
RESULT = {"ok": True, "written": "report.md"}


def _reopen(tmp_path) -> IdempotencyLog:
    """Simulate process restart by opening a fresh instance on the same DB."""
    return IdempotencyLog(tmp_path / "side_effects.db")


# -- crash point 1: intent written before crash ------------------------------

def test_crash_1_intent_written_before_crash(idem, tmp_path):
    """Crash after record_planned but before mark_executing. On restart,
    the row is in Planned state. sweep_stale transitions it to Uncertain.
    The engine's lookup returns state=uncertain -> NOT auto-replayed."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.close()

    reopened = _reopen(tmp_path)
    # The row is in Planned state (not committed).
    stale = reopened.uncommitted_for_run(RUN_ID)
    assert len(stale) == 1
    assert stale[0]["tool_call_id"] == CALL_ID
    assert stale[0]["state"] == SideEffectState.PLANNED.value

    # Cold-start sweep transitions to Uncertain.
    swept = reopened.sweep_stale([RUN_ID])
    assert len(swept) == 1
    assert swept[0]["tool_call_id"] == CALL_ID

    # Lookup now returns uncertain, not a replay.
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit is not None
    assert hit["state"] == "uncertain"
    assert hit["result"] is None

    # Uncertain side effects are visible for user resolution.
    uncertain = reopened.uncertain_for_run(RUN_ID)
    assert len(uncertain) == 1
    assert uncertain[0]["tool_call_id"] == CALL_ID
    reopened.close()


# -- crash point 2: crash after intent, before execute -----------------------

def test_crash_2_after_intent_before_execute(idem, tmp_path):
    """Same as crash 1 but the row was transitioned to Executing before
    the crash. The sweep still transitions it to Uncertain."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.close()

    reopened = _reopen(tmp_path)
    stale = reopened.uncommitted_for_run(RUN_ID)
    assert stale[0]["state"] == SideEffectState.EXECUTING.value

    swept = reopened.sweep_stale([RUN_ID])
    assert len(swept) == 1

    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "uncertain"
    reopened.close()


# -- crash point 3: crash during external execution --------------------------

def test_crash_3_during_execution(idem, tmp_path):
    """The tool was executing (Executing state) when the process crashed.
    Same sweep path: Executing -> Uncertain. NOT auto-replayed."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])

    # The side effect is Uncertain — the engine must NOT replay it.
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit is not None
    assert hit["state"] == "uncertain"
    reopened.close()


# -- crash point 4: crash after external success, before commit -------------

def test_crash_4_after_success_before_commit(idem, tmp_path):
    """The tool returned ok but the process crashed before commit() was
    called. The row is still in Executing state. Same Uncertain path."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    # Tool returned ok here — but commit() was never called.
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])

    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "uncertain"
    reopened.close()


# -- crash point 5: crash after commit, before tool result in messages -------

def test_crash_5_after_commit_before_result_message(idem, tmp_path):
    """Commit() succeeded but the tool result was not yet appended to
    messages. On resume, lookup returns Committed -> the engine replays
    the recorded result (no re-execution)."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.commit(RUN_ID, CALL_ID, TOOL, ARGS, RESULT)
    idem.close()

    reopened = _reopen(tmp_path)
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit is not None
    assert hit["state"] == "committed"
    assert hit["result"] == RESULT

    # Committed side effects are listed for audit.
    committed = reopened.committed_for_run(RUN_ID)
    assert len(committed) == 1
    assert committed[0]["result"] == RESULT
    reopened.close()


# -- crash point 6: crash after tool result written to messages --------------

def test_crash_6_after_result_written(idem, tmp_path):
    """Everything completed (commit + result message written). On resume
    the engine replays the recorded result — the side effect is not
    re-executed."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.commit(RUN_ID, CALL_ID, TOOL, ARGS, RESULT)
    idem.close()

    reopened = _reopen(tmp_path)
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "committed"
    assert hit["result"] == RESULT
    reopened.close()


# -- acceptance: committed side effects are NOT re-executed ------------------

def test_committed_not_reexecuted_on_resume(idem, tmp_path):
    """A committed side effect returns its recorded result on lookup —
    the engine replays instead of re-executing."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.commit(RUN_ID, CALL_ID, TOOL, ARGS, RESULT)
    idem.close()

    reopened = _reopen(tmp_path)
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "committed"
    assert hit["result"] == RESULT
    # No uncommitted or uncertain rows.
    assert reopened.uncommitted_for_run(RUN_ID) == []
    assert reopened.uncertain_for_run(RUN_ID) == []
    reopened.close()


# -- acceptance: Uncertain is NOT auto-replayed ------------------------------

def test_uncertain_not_auto_replayed(idem, tmp_path):
    """An Uncertain side effect's lookup returns state=uncertain with no
    result — the engine must NOT replay it."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])

    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "uncertain"
    assert hit["result"] is None
    reopened.close()


# -- acceptance: user can resolve Uncertain ----------------------------------

def test_resolve_uncertain_confirmed(idem, tmp_path):
    """User confirms the side effect did happen -> Committed."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])
    reopened.resolve_uncertain(RUN_ID, CALL_ID, "confirmed", result=RESULT)
    idem2 = reopened
    hit = idem2.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "committed"
    assert hit["result"] == RESULT
    reopened.close()


def test_resolve_uncertain_failed(idem, tmp_path):
    """User marks the side effect as failed (did NOT happen) -> Failed."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])
    reopened.resolve_uncertain(RUN_ID, CALL_ID, "failed")
    # Failed rows are not replayable (lookup returns None for non-committed).
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit is None
    reopened.close()


def test_resolve_uncertain_dismissed(idem, tmp_path):
    """User dismisses the side effect -> Failed (out-of-band handled)."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])
    reopened.resolve_uncertain(RUN_ID, CALL_ID, "dismissed")
    hit = reopened.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit is None
    reopened.close()


# -- acceptance: resume state is explainable ---------------------------------

def test_resume_state_explainable(idem, tmp_path):
    """After recovery, the three queries tell a coherent story:
    committed, uncertain, and uncommitted (none in this scenario)."""
    idem.record_planned(RUN_ID, "call-1", TOOL, ARGS)
    idem.commit(RUN_ID, "call-1", TOOL, ARGS, RESULT)
    idem.record_planned(RUN_ID, "call-2", TOOL, {"path": "other.md"})
    idem.close()

    reopened = _reopen(tmp_path)
    reopened.sweep_stale([RUN_ID])

    committed = reopened.committed_for_run(RUN_ID)
    uncertain = reopened.uncertain_for_run(RUN_ID)
    uncommitted = reopened.uncommitted_for_run(RUN_ID)

    assert len(committed) == 1
    assert committed[0]["tool_call_id"] == "call-1"
    assert len(uncertain) == 1
    assert uncertain[0]["tool_call_id"] == "call-2"
    assert uncommitted == []
    reopened.close()


# -- operation_id stability ---------------------------------------------------

def test_operation_id_stable_across_restarts(idem, tmp_path):
    """The operation_id for a side effect is derived from (run_id,
    tool_call_id) — the same value across restarts, not a fresh random
    id per attempt."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    op_id_1 = operation_id(RUN_ID, CALL_ID)
    idem.close()

    reopened = _reopen(tmp_path)
    stale = reopened.uncommitted_for_run(RUN_ID)
    assert stale[0]["operation_id"] == op_id_1
    reopened.close()


def test_operation_id_different_for_different_calls():
    """Different (run_id, tool_call_id) pairs produce different operation_ids."""
    a = operation_id("run-1", "call-A")
    b = operation_id("run-1", "call-B")
    c = operation_id("run-2", "call-A")
    assert a != b
    assert a != c
    assert b != c


# -- ledger events -----------------------------------------------------------

def test_planned_emits_ledger_event(idem, ledger):
    idem.record_planned(
        RUN_ID, CALL_ID, TOOL, ARGS, ledger=ledger
    )
    events = ledger.events(RUN_ID)
    types = [e["type"] for e in events]
    assert "side_effect.planned" in types


def test_failed_emits_ledger_event(idem, ledger):
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.mark_failed(RUN_ID, CALL_ID, "connection refused", ledger=ledger)
    events = ledger.events(RUN_ID)
    types = [e["type"] for e in events]
    assert "side_effect.failed" in types


def test_uncertain_emits_ledger_event(idem, ledger):
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_uncertain(RUN_ID, CALL_ID, ledger=ledger)
    events = ledger.events(RUN_ID)
    types = [e["type"] for e in events]
    assert "side_effect.uncertain" in types


def test_commit_ledger_event_includes_operation_id(idem, ledger):
    idem.commit(RUN_ID, CALL_ID, TOOL, ARGS, RESULT, ledger=ledger)
    events = ledger.events(RUN_ID)
    commit_event = [e for e in events if e["type"] == "side_effect.committed"][0]
    assert "operation_id" in commit_event["payload"]
    assert commit_event["payload"]["operation_id"] == operation_id(RUN_ID, CALL_ID)


# -- duplicate prevention ----------------------------------------------------

def test_mark_failed_only_on_planned_or_executing(idem):
    """mark_failed does not transition a Committed row (the side effect
    happened; a later error is a different call)."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.commit(RUN_ID, CALL_ID, TOOL, ARGS, RESULT)
    # A late mark_failed should NOT override Committed.
    idem.mark_failed(RUN_ID, CALL_ID, "late error")
    hit = idem.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "committed"
    assert hit["result"] == RESULT


def test_mark_uncertain_only_on_planned_or_executing(idem):
    """mark_uncertain does not override a Committed row."""
    idem.record_planned(RUN_ID, CALL_ID, TOOL, ARGS)
    idem.mark_executing(RUN_ID, CALL_ID)
    idem.commit(RUN_ID, CALL_ID, TOOL, ARGS, RESULT)
    idem.mark_uncertain(RUN_ID, CALL_ID)
    hit = idem.lookup(RUN_ID, CALL_ID, ARGS)
    assert hit["state"] == "committed"


# -- schema migration --------------------------------------------------------

def test_legacy_db_migrates_cleanly(tmp_path):
    """A pre-P0A database (no state/operation_id/updated_at columns)
    opens without error, and existing rows default to state=committed."""
    db = tmp_path / "side_effects.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE side_effects (
            run_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_sha256 TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            committed_at REAL NOT NULL,
            PRIMARY KEY (run_id, tool_call_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO side_effects VALUES (?, ?, ?, ?, ?, ?)",
        ("run-legacy", "call-X", "write_file", args_sha256(ARGS),
         json.dumps(RESULT), time.time()),
    )
    conn.commit()
    conn.close()

    log = IdempotencyLog(db)
    hit = log.lookup("run-legacy", "call-X", ARGS)
    assert hit is not None
    assert hit["state"] == "committed"
    assert hit["result"] == RESULT
    log.close()
