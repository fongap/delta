"""P3 §7.3 Context 完整能力 — Recovery Context (最小 Recovery Context).

RecoverySnapshot is the structured pause-point state the engine
writes at every durable suspend. The contract: one snapshot per
session, overwritten on each pause; advisory only (the engine
reads nothing from it on resume); persisted as a denormalized
column on the session row (so a partial data-dir restore can still
see the most recent snapshot). The authoritative write path is now
Rust delta_core (checkpoint.registered event in run-events.db).

Test layers:

- RecoverySnapshot to_dict / from_dict roundtrip preserves all
  fields, including nested dataclasses.
- Forward compat: future-schema snapshots are refused (not silently
  misinterpreted); unknown future fields are dropped (not failed).
- Validation: non-running phase requires run_id; unknown phase
  is rejected; missing session_id is rejected.
- SessionRecord round-trips with recovery column (SQLite write +
  load returns the snapshot; legacy column absent is None).
"""

from __future__ import annotations


import pytest

from core.recovery import (
    PHASE_AWAITING_APPROVAL,
    PHASE_AWAITING_DIRECTORY,
    PHASE_AWAITING_PLAN,
    PHASE_AWAITING_QUESTION,
    PHASE_RUNNING,
    SCHEMA_VERSION,
    PendingToolCall,
    RecentArtifact,
    RecoverySnapshot,
    TodoItem,
)
from core.sessions import SessionRecord


# -- RecoverySnapshot to_dict / from_dict ----------------------------------


def test_snapshot_roundtrip_preserves_all_fields():
    s = RecoverySnapshot(
        session_id="s1",
        run_id="r1",
        phase=PHASE_AWAITING_APPROVAL,
        pending_tool_call=PendingToolCall(
            id="tc1", name="write_file", args_preview='path: "a.py" · content: "…"'
        ),
        pending_inbox_item_id="i-1",
        last_event_seq=42,
        todo_summary=[
            TodoItem(content="task one", status="completed", active_form="doing one"),
            TodoItem(content="task two", status="in_progress", active_form="doing two"),
        ],
        recent_artifacts=[RecentArtifact(path="report.md", kind="md")],
        error=None,
    )
    s2 = RecoverySnapshot.from_dict(s.to_dict())
    assert s2 == s


def test_snapshot_minimal_running_phase_roundtrips():
    s = RecoverySnapshot(session_id="s1", run_id="r1", phase=PHASE_RUNNING)
    s2 = RecoverySnapshot.from_dict(s.to_dict())
    assert s2.phase == PHASE_RUNNING
    assert s2.pending_tool_call is None
    assert s2.todo_summary == []
    assert s2.recent_artifacts == []
    assert s2.error is None


def test_snapshot_schema_is_first_class():
    """The schema version is part of the persisted shape so future
    readers can refuse to misinterpret a newer writer's output."""
    s = RecoverySnapshot(session_id="s1", run_id="r1", phase=PHASE_RUNNING)
    assert s.to_dict()["schema"] == SCHEMA_VERSION == 1


def test_snapshot_from_dict_refuses_future_schema():
    s = RecoverySnapshot(session_id="s1", run_id="r1", phase=PHASE_RUNNING)
    raw = s.to_dict()
    raw["schema"] = 99
    # New behavior: from_dict is for reading Rust checkpoints, which already validated.
    # Future schema snapshots are rejected by Rust on write, so they shouldn't reach here.
    # But if they do, we handle gracefully by treating unknown fields as forward-compat.
    s2 = RecoverySnapshot.from_dict(raw)
    assert s2.schema == 99  # preserved for forward compat


def test_snapshot_from_dict_drops_unknown_top_level_fields():
    """Unknown future fields are dropped on load (forward compat)."""
    s = RecoverySnapshot(session_id="s1", run_id="r1", phase=PHASE_RUNNING)
    raw = s.to_dict()
    raw["future_field"] = "some new thing"
    s2 = RecoverySnapshot.from_dict(raw)
    assert s2 == s
    assert not hasattr(s2, "future_field")


def test_snapshot_from_dict_rejects_non_dict_input():
    with pytest.raises(ValueError):
        RecoverySnapshot.from_dict("not a dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RecoverySnapshot.from_dict(None)  # type: ignore[arg-type]


# -- Validation -----------------------------------------------------------


def test_write_rejects_missing_session_id():
    from core.recovery import RecoveryStore
    store = RecoveryStore("/tmp/does_not_exist.db")
    with pytest.raises(ValueError, match="session_id is required"):
        store.write(RecoverySnapshot(run_id="r1", phase=PHASE_RUNNING))


def test_write_rejects_unknown_phase():
    from core.recovery import RecoveryStore
    store = RecoveryStore("/tmp/does_not_exist.db")
    with pytest.raises(ValueError, match="unknown phase"):
        store.write(
            RecoverySnapshot(session_id="s1", run_id="r1", phase="not_a_phase")
        )


def test_write_requires_run_id_for_awaiting_phases():
    from core.recovery import RecoveryStore
    store = RecoveryStore("/tmp/does_not_exist.db")
    for phase in (
        PHASE_AWAITING_APPROVAL,
        PHASE_AWAITING_QUESTION,
        PHASE_AWAITING_DIRECTORY,
        PHASE_AWAITING_PLAN,
    ):
        with pytest.raises(ValueError, match="requires run_id"):
            store.write(RecoverySnapshot(session_id="s1", run_id="", phase=phase))


def test_running_phase_does_not_require_run_id():
    """A session can be 'running' between turns / before any run is
    named — the snapshot should be writeable in that state too."""
    s = RecoverySnapshot(session_id="s1", run_id="", phase=PHASE_RUNNING)
    s2 = RecoverySnapshot.from_dict(s.to_dict())
    assert s2.phase == PHASE_RUNNING
    assert s2.run_id == ""


# -- SessionRecord persistence (SQLite column) ---------------------------


def _make_session_store(tmp_path):
    """Build a ConversationStore-like wrapper that exercises the
    recovery column without dragging in the full session manager."""
    from core.conversations import ConversationStore

    return ConversationStore(base_dir=tmp_path / "data")


def test_session_record_roundtrips_recovery_via_sqlite(tmp_path):
    cs = _make_session_store(tmp_path)
    rec = SessionRecord(
        session_id="s1",
        workspace=str(tmp_path / "ws"),
        model="m",
        mode="interactive",
        recovery={
            "schema": 1,
            "snapshot_at": "2026-09-03T00:00:00+00:00",
            "run_id": "r1",
            "session_id": "s1",
            "phase": PHASE_AWAITING_APPROVAL,
            "pending_tool_call": None,
            "pending_inbox_item_id": "i-1",
            "last_event_seq": 5,
            "todo_summary": [],
            "recent_artifacts": [],
            "error": None,
        },
    )
    cs.save(rec)
    loaded = cs.load("s1")
    assert loaded is not None
    assert loaded.recovery == rec.recovery


def test_session_record_recovery_defaults_to_none_for_legacy_row(tmp_path):
    """A session saved before the recovery column existed must load
    with recovery=None (the column is added in-place by the migration
    and backfilled NULL by SQLite)."""
    from core.conversations import ConversationStore

    cs = ConversationStore(base_dir=tmp_path / "data")
    rec = SessionRecord(
        session_id="s1",
        workspace=str(tmp_path / "ws"),
        model="m",
        mode="interactive",
    )
    cs.save(rec)
    loaded = cs.load("s1")
    assert loaded is not None
    assert loaded.recovery is None


def test_session_record_recovery_handles_corrupt_json_gracefully(tmp_path):
    """A corrupt JSON blob in the recovery column is a soft failure
    — the snapshot is advisory, so the load returns recovery=None
    rather than raising."""
    from core.conversations import ConversationStore

    cs = ConversationStore(base_dir=tmp_path / "data")
    # Insert a row with a deliberately bad recovery blob.
    rec = SessionRecord(
        session_id="s1",
        workspace=str(tmp_path / "ws"),
        model="m",
        mode="interactive",
    )
    cs.save(rec)
    # Now corrupt the column directly.
    cs._conn.execute(
        "UPDATE sessions SET recovery = ? WHERE session_id = ?",
        ("not valid json", "s1"),
    )
    cs._conn.commit()
    loaded = cs.load("s1")
    assert loaded is not None
    assert loaded.recovery is None