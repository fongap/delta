"""AF-01 / AF-02 regression: automation completion single ownership + JSON/SQL sync.

R5.1 buyoff criteria:
  * one real execution advances run_count by exactly 1 (never +2)
  * max_runs=1 -> exactly 1 run, run_count=1, task disabled (enabled=false)
  * max_runs=2 -> exactly 2 runs, run_count=2
  * after complete_run, the SQL indexed columns (enabled, next_run) match the
    JSON blob — `due_tasks()` never re-surfaces an exhausted task and never
    reads a stale enabled=1 for a JSON-disabled task.

The Scheduler is the single completion owner: the runner only executes and
returns a TaskRun; the scheduler calls TaskStore.complete_run exactly once.
"""

from __future__ import annotations

import asyncio
from datetime import timezone

from core.automation.models import Schedule, ScheduledTask, TaskRun
from core.automation.scheduler import Scheduler
from core.automation.store import TaskStore


def _now() -> float:
    return __import__("datetime").datetime.now(timezone.utc).timestamp()


def _task(*, max_runs: int | None, cron: str = "* * * * *") -> ScheduledTask:
    return ScheduledTask(
        title="t",
        instructions="noop",
        schedule=Schedule(kind="cron", cron=cron),
        max_runs=max_runs,
        workspace="",
    )


async def _drive(store: TaskStore, task: ScheduledTask, runner_calls: list) -> None:
    """Directly drive one scheduler run against a runner returning a bare
    TaskRun. The runner does NOT call complete_run — the scheduler alone
    finalizes through ``_execute``."""

    async def runner(_t, _trigger):
        runner_calls.append(_t.id)
        return TaskRun(task_id=_t.id, status="ok")

    sched = Scheduler(store, runner, tick_seconds=3600)
    await sched.run_task(task, trigger="manual")
    await sched.stop()


def test_single_execution_advances_run_count_by_one(tmp_path):
    """AF-01: one real execution must not double-increment run_count."""
    store = TaskStore(tmp_path / "auto.db")
    t = _task(max_runs=None)
    store.save(t)

    calls: list = []
    asyncio.run(_drive(store, t, calls))

    assert calls == [t.id], f"runner invoked {len(calls)} time(s)"
    advanced = store.get(t.id)
    assert advanced is not None
    assert advanced.run_count == 1, f"run_count must be 1, got {advanced.run_count}"
    assert advanced.last_status == "ok"


def test_max_runs_one_disables_task_in_json_and_sql(tmp_path):
    """AF-01 + AF-02: max_runs=1 runs once and the task is disabled with
    the SQL enabled column in sync with JSON (not left at 1)."""
    store = TaskStore(tmp_path / "auto.db")
    t = _task(max_runs=1)
    store.save(t)

    calls: list = []
    asyncio.run(_drive(store, t, calls))

    assert calls == [t.id], "max_runs=1 must run exactly once"
    advanced = store.get(t.id)
    assert advanced is not None
    assert advanced.run_count == 1
    assert advanced.enabled is False, "exhausted task must be disabled"

    # AF-02: due_tasks() (which reads the SQL indexed column) must not
    # re-surface the exhausted task even if next_run is in the past.
    assert store.due() == [], "exhausted task must not appear as due"


def test_max_runs_two_runs_exactly_twice(tmp_path):
    """AF-01: max_runs=2 runs exactly twice, then disables."""
    store = TaskStore(tmp_path / "auto.db")
    t = _task(max_runs=2)
    store.save(t)

    # Run 1
    calls: list = []
    asyncio.run(_drive(store, t, calls))
    t2 = store.get(t.id)
    assert t2 is not None
    assert t2.run_count == 1 and t2.enabled is True

    # Run 2
    calls2: list = []
    asyncio.run(_drive(store, t, calls2))
    t3 = store.get(t.id)
    assert t3 is not None
    assert t3.run_count == 2
    assert t3.enabled is False, "after max_runs reaches 2 the task disables"
    assert store.due() == []


def test_complete_run_syncs_sql_indexed_columns(tmp_path):
    """AF-02: after complete_run the indexed columns match the JSON blob.
    Directly inspect the SQLite row (not the Python facade) to prove there is
    no enabled=1 / next_run staleness behind the facade."""
    import sqlite3

    from core.automation.store import compute_next_run

    db_path = tmp_path / "auto.db"
    store = TaskStore(db_path)
    t = _task(max_runs=1)
    store.save(t)

    # Manually finalize one run through the single complete_run path.
    task_fresh = store.get(t.id)
    assert task_fresh is not None
    run = TaskRun(task_id=task_fresh.id, status="ok")
    projected = task_fresh
    projected.run_count = task_fresh.run_count + 1
    projected.enabled = not (task_fresh.max_runs and projected.run_count >= task_fresh.max_runs)
    nr = compute_next_run(projected) if projected.enabled else None
    store.complete_run(run, _now(), next_run=nr)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT enabled, next_run, data FROM scheduled_tasks WHERE id = ?",
        (task_fresh.id,),
    ).fetchone()
    conn.close()
    assert row is not None
    sql_enabled, sql_next_run, data = row
    import json

    blob = json.loads(data)
    assert sql_enabled == 0, f"SQL enabled must be 0 (disabled), got {sql_enabled}"
    assert blob["enabled"] is False
    assert sql_next_run is None, "SQL next_run must be NULL for exhausted task"
    assert blob["next_run"] is None


def test_runner_exception_also_finalizes_once(tmp_path):
    """AF-01: even when the runner throws, the scheduler finalizes the error
    run once via complete_run (run_count +1, not double)."""
    store = TaskStore(tmp_path / "auto.db")
    t = _task(max_runs=None)
    store.save(t)

    async def bad_runner(_t, _trigger):
        raise RuntimeError("boom")

    async def drive():
        sched = Scheduler(store, bad_runner, tick_seconds=3600)
        await sched.run_task(t, trigger="manual")
        await sched.stop()

    asyncio.run(drive())

    advanced = store.get(t.id)
    assert advanced is not None
    assert advanced.run_count == 1, f"error run must count once, got {advanced.run_count}"
    assert advanced.last_status == "error"