"""P3 §10.6 路径: ``run_events`` + ``task_runs`` 上的 ``workspace`` 列.

Adds a denormalized ``workspace`` column on two primary tables so the
P3 Run Analyzer (and any future per-workspace query) can scope to a
workspace without re-deriving it from ``payload.workspace`` JSON. The
column is **not** part of the hash chain (ADR-005), so:

- a pre-migration DB opens fine (column is added via ALTER TABLE; old
  rows have workspace = NULL → reported as ``""`` in dicts);
- a row's workspace can be backfilled without breaking ``verify()``;
- new appends accept an optional ``workspace=`` kwarg.

These tests pin all three contracts plus the round-trip on TaskRun.
"""

from __future__ import annotations

import json
import sqlite3

from core.automation.models import TaskRun
from core.automation.store import TaskStore
from core.ledger import RunEventLedger


# -- ledger: append + verify + new column ----------------------------------


def test_new_ledger_has_workspace_column(tmp_path):
    """A fresh DB has the column at CREATE TABLE time (no ALTER needed)."""
    led = RunEventLedger(tmp_path / "events.db")
    cols = [
        r[1] for r in led._conn.execute("PRAGMA table_info(run_events)").fetchall()
    ]
    assert "workspace" in cols
    led.close()


def test_legacy_db_migrates_to_workspace_column(tmp_path):
    """A DB built before the column existed picks it up via ALTER TABLE."""
    db = tmp_path / "legacy.db"
    # Build a "legacy" schema by hand: no workspace column.
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            ts REAL NOT NULL,
            actor TEXT NOT NULL DEFAULT 'system',
            payload TEXT NOT NULL DEFAULT '{}',
            prev_hash TEXT NOT NULL DEFAULT '',
            hash TEXT NOT NULL
        )
        """)
    conn.execute(
        "INSERT INTO run_events (run_id, seq, type, ts, actor, payload, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy-run", 1, "run.started", 1000.0, "system", "{}", "", "h1"),
    )
    conn.commit()
    conn.close()

    led = RunEventLedger(db)
    try:
        # Column now exists thanks to the ALTER TABLE migration.
        cols = [
            r[1]
            for r in led._conn.execute("PRAGMA table_info(run_events)").fetchall()
        ]
        assert "workspace" in cols
        # Legacy row reads back with workspace = "" (NULL → empty).
        events = led.events("legacy-run")
        assert len(events) == 1
        assert events[0]["workspace"] == ""
    finally:
        led.close()


def test_new_append_carries_workspace_column(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    try:
        led.append(
            "run-1",
            "run.started",
            actor="system",
            payload={"kind": "run"},
            workspace="/path/to/ws",
        )
        events = led.events("run-1")
        assert events[0]["workspace"] == "/path/to/ws"
    finally:
        led.close()


def test_workspace_absent_becomes_empty_string(tmp_path):
    """Backwards compat: existing call sites that don't pass
    ``workspace=`` still work and report ``""`` from ``events()``."""
    led = RunEventLedger(tmp_path / "events.db")
    try:
        led.append("run-1", "run.started", actor="system")
        led.append("run-1", "run.completed", actor="system")
        for ev in led.events("run-1"):
            assert ev["workspace"] == ""
    finally:
        led.close()


def test_workspace_not_part_of_hash_basis(tmp_path):
    """Two appends with different workspaces but otherwise identical
    payload + actor + ts would have the same hash basis (workspace is
    *not* hashed). This is the contract that lets backfills change the
    column without breaking verify()."""
    led = RunEventLedger(tmp_path / "events.db")
    try:
        # Force identical ts so the hash basis matches.
        led.append(
            "r1",
            "run.started",
            actor="system",
            payload={"kind": "run"},
            ts=1000.0,
            workspace="/ws-A",
        )
        led.append(
            "r2",
            "run.started",
            actor="system",
            payload={"kind": "run"},
            ts=1000.0,
            workspace="/ws-B",
        )
        a = led.events("r1")[0]
        b = led.events("r2")[0]
        # Same hash basis → same hash (workspace excluded).
        assert a["hash"] == b["hash"]
        assert led.verify("r1") and led.verify("r2")
    finally:
        led.close()


def test_workspace_accepts_none(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    try:
        led.append("r1", "run.started", workspace=None)
        ev = led.events("r1")[0]
        assert ev["workspace"] == ""
    finally:
        led.close()


# -- task_runs: schema + add_run + find_run + from_dict ----------------------


def test_task_runs_has_workspace_column(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    try:
        cols = [
            r[1] for r in store._conn.execute("PRAGMA table_info(task_runs)").fetchall()
        ]
        assert "workspace" in cols
    finally:
        store.close()


def test_task_runs_legacy_db_migrates(tmp_path):
    """Pre-migration DB gets the column via ALTER TABLE."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE task_runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            started_at REAL NOT NULL,
            data TEXT NOT NULL
        )
        """)
    conn.execute(
        "INSERT INTO task_runs (run_id, task_id, started_at, data) VALUES (?, ?, ?, ?)",
        ("run-legacy", "task-1", 1000.0, json.dumps({"task_id": "task-1"})),
    )
    conn.commit()
    conn.close()

    store = TaskStore(db)
    try:
        cols = [
            r[1]
            for r in store._conn.execute("PRAGMA table_info(task_runs)").fetchall()
        ]
        assert "workspace" in cols
        # Legacy row still loads; workspace is the dataclass default.
        run = store.find_run("run-legacy")
        assert run is not None
        assert run.workspace == ""
    finally:
        store.close()


def test_task_run_to_from_dict_round_trip_includes_workspace():
    """The new field round-trips through to_dict / from_dict so JSON
    storage keeps the workspace on save/load."""
    run = TaskRun(
        task_id="t1",
        run_id="r1",
        started_at=1000.0,
        status="ok",
        workspace="/path/to/ws",
    )
    blob = run.to_dict()
    assert blob["workspace"] == "/path/to/ws"
    rehydrated = TaskRun.from_dict(blob)
    assert rehydrated.workspace == "/path/to/ws"


def test_task_run_from_dict_backfills_missing_workspace():
    """Old payloads without the field land on the default."""
    legacy = {
        "task_id": "t1",
        "run_id": "r1",
        "started_at": 1000.0,
        "finished_at": None,
        "status": "ok",
        "result_text": None,
        "artifacts": [],
        "error": None,
        "trigger": "schedule",
        "session_id": "",
    }
    run = TaskRun.from_dict(legacy)
    assert run.workspace == ""  # dataclass default, not an error


def test_add_run_persists_workspace(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    try:
        run = TaskRun(task_id="t1", run_id="r1", workspace="/ws-A")
        store.add_run(run)
        # Read back from SQL directly so we know the column actually got
        # written, not just round-tripped via JSON.
        row = store._conn.execute(
            "SELECT workspace FROM task_runs WHERE run_id=?", ("r1",)
        ).fetchone()
        assert row is not None
        assert row["workspace"] == "/ws-A"
        # And the in-memory round trip also preserves it.
        rehydrated = store.find_run("r1")
        assert rehydrated is not None
        assert rehydrated.workspace == "/ws-A"
    finally:
        store.close()


def test_add_run_workspace_round_trip_via_runs_query(tmp_path):
    """``store.runs(task_id)`` returns the workspace on each row."""
    store = TaskStore(tmp_path / "tasks.db")
    try:
        for i, ws in enumerate(["/ws-A", "/ws-A", "/ws-B"]):
            store.add_run(
                TaskRun(
                    task_id="t1",
                    run_id=f"r{i}",
                    started_at=1000.0 + i,
                    workspace=ws,
                )
            )
        runs = store.runs("t1")
        assert sorted(r.workspace for r in runs) == ["/ws-A", "/ws-A", "/ws-B"]
    finally:
        store.close()


# -- ledger ↔ Analyzer: workspace column reaches the projection -------------


def test_ledger_events_returns_workspace_for_analyzer_filter(tmp_path):
    """The P3 Run Analyzer consumes ``ledger.events(run_id)``; the new
    column flows through _as_dict so the Analyzer (or any future
    per-workspace query) can scope to a workspace directly."""
    from core.analyzer import Analyzer

    ws = tmp_path / "ws"
    ws.mkdir()
    led = RunEventLedger(tmp_path / "events.db")
    try:
        led.append("run-1", "run.started", workspace=str(ws))
        led.append("run-1", "run.completed", workspace=str(ws))
        # The Analyzer is bound to a workspace; it reads the workspace
        # from each ledger row by introspection (or, as today, falls
        # back to its own binding). For this contract test we just
        # confirm the column is reachable from a plain ledger read.
        events = led.events("run-1")
        assert all(e["workspace"] == str(ws) for e in events)
        # And the Analyzer can still be constructed + used.
        Analyzer(workspace=str(ws), ledger=led).timeline_for_run("run-1")
    finally:
        led.close()
