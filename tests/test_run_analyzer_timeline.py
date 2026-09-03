"""P3 Run Analyzer — ``timeline_for_run`` end-to-end reconstruction.

The timeline is a 1:1 projection of the ledger rows for a run, in
``seq`` order. Tests assert the shape (order, types, payload access)
on a freshly-built ledger so a future refactor that adds an
"intermediate cache" would still be measured against the source of
truth.
"""

from __future__ import annotations

from core.analyzer import Analyzer, TimelineEntry, timeline_for_run
from core.ledger import RunEventLedger


def _ledger_with_run(tmp_path, run_id: str = "run-1") -> RunEventLedger:
    led = RunEventLedger(tmp_path / "events.db")
    led.append(run_id, "run.started", actor="system", payload={"session_id": "s1"})
    led.append(
        run_id,
        "tool.proposed",
        actor="engine",
        payload={"tool": "read_file", "args": {"path": "a.txt"}},
    )
    led.append(
        run_id,
        "tool.finished",
        actor="engine",
        payload={"tool": "read_file", "status": "ok", "preview": "hi"},
    )
    led.append(
        run_id,
        "validation.passed",
        actor="validator",
        payload={"check": "artifact_count"},
    )
    led.append(run_id, "run.completed", actor="system", payload={"summary": "ok"})
    return led


def test_timeline_orders_by_seq(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = _ledger_with_run(tmp_path, run_id="run-1")
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led)
        tl = analyzer.timeline_for_run("run-1")
    finally:
        led.close()
    assert [e.seq for e in tl] == [1, 2, 3, 4, 5]
    assert [e.type for e in tl] == [
        "run.started",
        "tool.proposed",
        "tool.finished",
        "validation.passed",
        "run.completed",
    ]


def test_timeline_entries_carry_payload_and_actor(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = _ledger_with_run(tmp_path)
    try:
        tl = Analyzer(workspace=str(ws), ledger=led).timeline_for_run("run-1")
    finally:
        led.close()
    tool_finished = next(e for e in tl if e.type == "tool.finished")
    assert tool_finished.actor == "engine"
    assert tool_finished.payload["tool"] == "read_file"
    assert tool_finished.payload["status"] == "ok"


def test_timeline_does_not_mutate_ledger(tmp_path):
    """The projection is read-only — mutating the returned list does
    not change the underlying ledger."""
    ws = tmp_path / "ws"
    ws.mkdir()
    led = _ledger_with_run(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led)
        tl = analyzer.timeline_for_run("run-1")
        original_len = len(tl)
        # Mutate the timeline; second call must not be affected.
        tl.append(TimelineEntry(seq=99, type="evil", actor="x", ts=0.0))
        tl.clear()
        tl2 = analyzer.timeline_for_run("run-1")
    finally:
        led.close()
    assert len(tl2) == original_len


def test_timeline_for_unknown_run_is_empty(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = _ledger_with_run(tmp_path, run_id="run-1")
    try:
        tl = Analyzer(workspace=str(ws), ledger=led).timeline_for_run("run-missing")
    finally:
        led.close()
    assert tl == []


def test_timeline_module_wrapper_matches_class(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = _ledger_with_run(tmp_path)
    try:
        via_class = Analyzer(workspace=str(ws), ledger=led).timeline_for_run("run-1")
        via_module = timeline_for_run(workspace=str(ws), run_id="run-1", ledger=led)
    finally:
        led.close()
    assert [e.to_dict() for e in via_class] == [e.to_dict() for e in via_module]


def test_timeline_to_dict_round_trip(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = _ledger_with_run(tmp_path)
    try:
        tl = Analyzer(workspace=str(ws), ledger=led).timeline_for_run("run-1")
    finally:
        led.close()
    d = tl[0].to_dict()
    assert set(d) == {"seq", "type", "actor", "ts", "payload"}
