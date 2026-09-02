"""Tests for ADR-005 WS4: IdempotencyLog.

The blueprint requires: "Tool 已产生副作用但 Run 未结束 → 恢复后不重复执行".

The log records `(run_id, tool_call_id, args_sha256, result)` after a
consequential tool commits. On resume the engine looks up the same call:

  - same (run_id, tool_call_id, args)  → replay the recorded result
  - different args                     → treat as a fresh call, re-execute
  - no row                             → fresh call

These tests exercise the contract directly. The engine wiring (WS4
integration) is exercised in test_durable_resume.py once the engine
consults the log before re-executing a side-effecting call.
"""

from __future__ import annotations


import pytest

from core.idemlog import IdempotencyLog, args_sha256
from core.ledger import RunEventLedger


@pytest.fixture
def idem(tmp_path) -> IdempotencyLog:
    inst = IdempotencyLog(tmp_path / "side_effects.db")
    yield inst
    inst.close()


@pytest.fixture
def ledger(tmp_path_factory) -> RunEventLedger:
    inst = RunEventLedger(tmp_path_factory.mktemp("ledger") / "run-events.db")
    yield inst
    inst.close()


def test_args_sha256_is_key_order_independent():
    a = args_sha256({"path": "/x.md", "text": "hi"})
    b = args_sha256({"text": "hi", "path": "/x.md"})
    assert a == b


def test_args_sha256_changes_with_value():
    a = args_sha256({"path": "/x.md", "text": "hi"})
    b = args_sha256({"path": "/x.md", "text": "bye"})
    assert a != b


def test_commit_then_lookup_returns_recorded_result(idem):
    idem.commit("run-1", "call-A", "write_file", {"path": "/x.md"}, {"written": True})
    hit = idem.lookup("run-1", "call-A", {"path": "/x.md"})
    assert hit is not None
    assert hit["tool_name"] == "write_file"
    assert hit["result"] == {"written": True}


def test_lookup_fresh_call_returns_none(idem):
    assert idem.lookup("run-1", "call-A", {"path": "/x.md"}) is None


def test_lookup_with_different_args_returns_none(idem):
    """Same tool_call_id but different arguments = a DIFFERENT call. We never
    replay a result that belongs to different work."""
    idem.commit("run-1", "call-A", "write_file", {"path": "/x.md"}, {"written": True})
    assert idem.lookup("run-1", "call-A", {"path": "/y.md"}) is None


def test_lookup_is_scoped_to_run(idem):
    idem.commit("run-1", "call-A", "write_file", {"path": "/x.md"}, {"written": True})
    assert idem.lookup("run-2", "call-A", {"path": "/x.md"}) is None


def test_recommit_is_idempotent(idem):
    """A duplicate commit (crash between the INSERT and the ledger event, then
    retry) overwrites cleanly — no constraint error, no duplicate row."""
    idem.commit("run-1", "call-A", "write_file", {"path": "/x.md"}, {"written": True})
    idem.commit("run-1", "call-A", "write_file", {"path": "/x.md"}, {"written": True})
    hit = idem.lookup("run-1", "call-A", {"path": "/x.md"})
    assert hit["result"] == {"written": True}


def test_commit_emits_ledger_event(idem, ledger):
    idem.commit(
        "run-1",
        "call-A",
        "run_shell",
        {"command": "ls"},
        {"exit_code": 0},
        ledger=ledger,
    )
    events = ledger.events("run-1")
    types = [e["type"] for e in events]
    assert "side_effect.committed" in types
    payload = events[-1]["payload"]
    assert payload["tool"] == "run_shell"
    assert payload["tool_call_id"] == "call-A"


def test_commit_survives_reopen(tmp_path):
    """The whole point is surviving a crash + restart. Write in one process,
    read from a fresh instance pointing at the same file."""
    db = tmp_path / "side_effects.db"
    a = IdempotencyLog(db)
    a.commit("run-1", "call-A", "send_email", {"to": "x@y.z"}, {"sent": True})
    a.close()

    b = IdempotencyLog(db)
    hit = b.lookup("run-1", "call-A", {"to": "x@y.z"})
    assert hit is not None
    assert hit["result"] == {"sent": True}
    b.close()


def test_empty_run_id_is_ignored(idem):
    """No run scope → nothing to dedupe (background teardown). commit is a
    no-op; lookup is always None."""
    idem.commit("", "call-A", "write_file", {"path": "/x.md"}, {"written": True})
    assert idem.lookup("", "call-A", {"path": "/x.md"}) is None
