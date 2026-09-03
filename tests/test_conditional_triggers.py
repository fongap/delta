"""P3 §7.3 §734 — conditional Automation triggers (Trigger + TriggerRegistry).

A task gets either a Schedule (time-based, the legacy path) or a
Trigger (event-based, this PR). The same Task / Run Runtime handles
both; the trigger only chooses *when* to fire.

Three trigger sources in v1:
- ``manual`` — explicit tool push; event carries a task_id (or the
  trigger has a configured task_id)
- ``filesystem`` — glob on the task's workspace; an FS event with
  a matching path dispatches
- ``inbox`` — kind + data_match filter on Inbox items

Contract:

- A task with ``trigger != None`` is event-driven; the time-based
  scheduler's ``due()`` query skips it (``next_run = None``)
- :class:`TriggerRegistry` maps task_id -> Trigger; methods:
  ``add``, ``remove``, ``get``, ``list``, ``clear``,
  ``hydrate_from_store``, ``dispatch``
- ``dispatch(event)`` returns the list of task_ids to run (the
  caller decides how to run them); never raises
- Cooldown per trigger (``cooldown_seconds``): rapid re-fires are
  deduplicated both per-trigger and per-(task, event fingerprint)
- Bad events (missing source, malformed payload) are dropped with a
  warning — dispatch must not tear down the scheduler
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.automation.models import Schedule, ScheduledTask
from core.automation.store import TaskStore, compute_next_run
from core.automation.triggers import (
    SOURCE_FILESYSTEM,
    SOURCE_INBOX,
    SOURCE_MANUAL,
    Trigger,
    TriggerRegistry,
    _validate_condition,
)


# -- _validate_condition direct ------------------------------------------


def test_validate_condition_accepts_known_sources():
    _validate_condition({"source": SOURCE_MANUAL, "condition": {}})
    _validate_condition({"source": SOURCE_FILESYSTEM, "glob": "inbox/*.csv"})
    _validate_condition({"source": SOURCE_INBOX, "kind": "approval", "data_match": {"task_id": "t1"}})


def test_validate_condition_rejects_unknown_source():
    with pytest.raises(ValueError, match="unknown trigger source"):
        _validate_condition({"source": "webrtc"})


def test_validate_condition_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="filesystem trigger needs a 'glob'"):
        _validate_condition({"source": SOURCE_FILESYSTEM})
    with pytest.raises(ValueError, match="inbox trigger needs a 'kind'"):
        _validate_condition({"source": SOURCE_INBOX})


# -- TriggerRegistry dispatch ---------------------------------------------


def test_dispatch_manual_matches_configured_task_id():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(source=SOURCE_MANUAL, condition={"task_id": "t1"}),
    )
    assert reg.dispatch({"source": SOURCE_MANUAL, "task_id": "t1"}) == ["t1"]


def test_dispatch_manual_matches_unconditional_trigger():
    """A manual trigger with no task_id in its condition matches any
    event on its own source — useful for the test/dev path that just
    wants 'fire me now' regardless of which task id is in the event."""
    reg = TriggerRegistry()
    reg.add("t1", Trigger(source=SOURCE_MANUAL, condition={}))
    assert reg.dispatch({"source": SOURCE_MANUAL}) == ["t1"]


def test_dispatch_manual_rejects_wrong_task_id():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(source=SOURCE_MANUAL, condition={"task_id": "t1"}),
    )
    assert reg.dispatch({"source": SOURCE_MANUAL, "task_id": "other"}) == []


def test_dispatch_filesystem_glob_match():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(source=SOURCE_FILESYSTEM, condition={"glob": "inbox/*.csv"}),
    )
    matches = reg.dispatch(
        {"source": SOURCE_FILESYSTEM, "path": "inbox/q3.csv"}
    )
    assert matches == ["t1"]


def test_dispatch_filesystem_prefix_match():
    """A glob without wildcards acts as a directory prefix — common
    case of "any new file under this dir"."""
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(source=SOURCE_FILESYSTEM, condition={"glob": str(Path("/tmp/inbox"))}),
    )
    matches = reg.dispatch(
        {"source": SOURCE_FILESYSTEM, "path": str(Path("/tmp/inbox") / "x.csv")}
    )
    assert matches == ["t1"]


def test_dispatch_filesystem_no_match():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(source=SOURCE_FILESYSTEM, condition={"glob": "inbox/*.csv"}),
    )
    matches = reg.dispatch(
        {"source": SOURCE_FILESYSTEM, "path": "elsewhere/x.txt"}
    )
    assert matches == []


def test_dispatch_inbox_kind_match():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(
            source=SOURCE_INBOX,
            condition={"kind": "approval", "data_match": {"task_id": "t1"}},
        ),
    )
    matches = reg.dispatch(
        {
            "source": SOURCE_INBOX,
            "kind": "approval",
            "id": "i1",
            "data": {"task_id": "t1", "tool": "write_file"},
        }
    )
    assert matches == ["t1"]


def test_dispatch_inbox_kind_mismatch_skipped():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(source=SOURCE_INBOX, condition={"kind": "approval"}),
    )
    assert (
        reg.dispatch({"source": SOURCE_INBOX, "kind": "question", "data": {}}) == []
    )


def test_dispatch_inbox_data_match_subset_check():
    """data_match is a subset check — every expected key/value must
    match, but the event can carry additional keys."""
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(
            source=SOURCE_INBOX,
            condition={"kind": "approval", "data_match": {"task_id": "t1"}},
        ),
    )
    assert reg.dispatch(
        {
            "source": SOURCE_INBOX,
            "kind": "approval",
            "id": "i1",
            "data": {"task_id": "t1", "tool": "write_file", "extra": "ignored"},
        }
    ) == ["t1"]
    # different task_id → no match
    assert (
        reg.dispatch(
            {
                "source": SOURCE_INBOX,
                "kind": "approval",
                "id": "i2",
                "data": {"task_id": "other"},
            }
        )
        == []
    )


def test_dispatch_missing_source_returns_empty():
    reg = TriggerRegistry()
    reg.add("t1", Trigger(source=SOURCE_MANUAL, condition={}))
    assert reg.dispatch({}) == []


def test_dispatch_skips_other_source_triggers():
    """A filesystem event must not fire a manual trigger (or vice versa)."""
    reg = TriggerRegistry()
    reg.add("t1", Trigger(source=SOURCE_MANUAL, condition={}))
    reg.add(
        "t2", Trigger(source=SOURCE_FILESYSTEM, condition={"glob": "*.csv"})
    )
    # An FS event hits t2 only.
    assert reg.dispatch({"source": SOURCE_FILESYSTEM, "path": "x.csv"}) == ["t2"]
    # A manual event hits t1 only.
    assert reg.dispatch({"source": SOURCE_MANUAL}) == ["t1"]


def test_dispatch_cooldown_dedups_rapid_refires():
    reg = TriggerRegistry()
    reg.add(
        "t1",
        Trigger(
            source=SOURCE_MANUAL,
            condition={},
            cooldown_seconds=60.0,
        ),
    )
    # First dispatch: fires.
    assert reg.dispatch({"source": SOURCE_MANUAL}) == ["t1"]
    # Second dispatch within the cooldown window: skipped.
    assert reg.dispatch({"source": SOURCE_MANUAL}) == []
    # A different event (different fingerprint) is also deduped within
    # the cooldown window because the cooldown is per-trigger.
    assert (
        reg.dispatch({"source": SOURCE_MANUAL, "task_id": "anything"}) == []
    )


def test_dispatch_returns_multiple_matching_tasks():
    reg = TriggerRegistry()
    reg.add("t1", Trigger(source=SOURCE_MANUAL, condition={}))
    reg.add("t2", Trigger(source=SOURCE_MANUAL, condition={}))
    assert sorted(reg.dispatch({"source": SOURCE_MANUAL})) == ["t1", "t2"]


# -- TriggerRegistry persistence / hydration -----------------------------


def test_hydrate_from_store_loads_triggers(tmp_path):
    """The TaskStore persists triggers as part of the JSON blob; the
    registry rebuilds its in-memory map from a list of task records."""
    store = TaskStore(tmp_path / "tasks.db")
    t1 = ScheduledTask(
        title="a",
        instructions="x",
        schedule=Schedule(kind="once", fire_at="2099-01-01"),
        workspace=str(tmp_path / "ws1"),
        trigger=Trigger(source=SOURCE_MANUAL, condition={}),
    )
    t2 = ScheduledTask(
        title="b",
        instructions="y",
        schedule=Schedule(kind="once", fire_at="2099-01-01"),
        workspace=str(tmp_path / "ws2"),
        # No trigger — legacy time-based task.
    )
    store.save(t1)
    store.save(t2)
    reg = TriggerRegistry()
    reg.hydrate_from_store(store.list())
    assert set(reg.list().keys()) == {t1.id}
    assert reg.get(t1.id).source == SOURCE_MANUAL


def test_legacy_task_without_trigger_field_hydrates_cleanly(tmp_path):
    """Old task rows (no trigger field) must hydrate without errors."""
    store = TaskStore(tmp_path / "tasks.db")
    t = ScheduledTask(
        title="legacy",
        instructions="x",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(tmp_path / "ws"),
    )
    store.save(t)
    reg = TriggerRegistry()
    reg.hydrate_from_store(store.list())
    assert reg.list() == {}


# -- ScheduledTask + compute_next_run integration -------------------------


def test_event_driven_task_has_no_next_run(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    t = ScheduledTask(
        title="event",
        instructions="x",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),  # legacy field
        workspace=str(tmp_path / "ws"),
        trigger=Trigger(source=SOURCE_MANUAL, condition={}),
    )
    store.save(t)
    # Time-based next_run is None because the task is event-driven.
    assert compute_next_run(t) is None
    # And the store's due() query doesn't return it.
    assert store.due() == []


def test_legacy_time_task_still_has_next_run(tmp_path):
    """Sanity: time-based tasks without trigger are unaffected."""
    store = TaskStore(tmp_path / "tasks.db")
    t = ScheduledTask(
        title="time",
        instructions="x",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(tmp_path / "ws"),
    )
    store.save(t)
    # Just-check: next_run is computed (don't pin the value).
    assert compute_next_run(t) is not None
    # store.due() can return it; we don't assert which.


def test_trigger_round_trips_through_store_json(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    trig = Trigger(
        source=SOURCE_FILESYSTEM,
        condition={"glob": "inbox/*.csv"},
        cooldown_seconds=30.0,
        last_fired_at=1700000000.0,
    )
    t = ScheduledTask(
        title="fs task",
        instructions="process new csvs",
        schedule=Schedule(kind="once", fire_at="2099-01-01"),
        workspace=str(tmp_path / "ws"),
        trigger=trig,
    )
    store.save(t)
    loaded = store.get(t.id)
    assert loaded is not None
    assert isinstance(loaded.trigger, Trigger)
    assert loaded.trigger.source == SOURCE_FILESYSTEM
    assert loaded.trigger.condition["glob"] == "inbox/*.csv"
    assert loaded.trigger.cooldown_seconds == 30.0
    assert loaded.trigger.last_fired_at == 1700000000.0
