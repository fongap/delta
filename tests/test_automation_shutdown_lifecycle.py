"""R9 contract: automation shutdown lifecycle.

The scheduler's `stop()` cancels in-flight runs. The contract:

  1. A `running` task is never left in the store after stop() returns —
     the `TaskRun.status` reflects what the engine actually got to write
     (error or whatever the runner finished with).
  2. A subsequent restart + tick does not double-fire the catch-up. The
     server's existing `recover_stale` (core/ledger.py + services/server/run.py)
     already handles the canonical "interrupted" run recovery; the scheduler
     does not need a second catch-up path.
  3. Committed side effects (a tool call that already wrote to the
     idemlog) are NOT re-played on the next start. That's the
     `IdempotencyLog` invariant from the Reliable Task Runtime (P1).

This file tests the scheduler surface only. The idemlog / recoverable
restart tests already live in `tests/test_reference_task.py`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from core.automation.models import Schedule, ScheduledTask, TaskRun
from core.automation.scheduler import Scheduler
from core.automation.store import TaskStore


def _now() -> float:
    return time.time()


def _task(*, next_run: float | None = None) -> ScheduledTask:
    return ScheduledTask(
        title="t1",
        instructions="hello",
        workspace=str(Path("/tmp")),
        schedule=Schedule(kind="cron", cron="* * * * *", timezone="local"),
        next_run=next_run if next_run is not None else _now(),
    )


def _insert_due(store: TaskStore, task: ScheduledTask, *, next_run: float) -> ScheduledTask:
    """Insert a task directly into the store with an explicit `next_run`.

    `TaskStore.save` always overwrites `next_run` via `compute_next_run` (which
    only returns a value in the future), so we can't seed a "due" row by
    going through `save`. Bypass the API for fixture purposes: this is the
    shape `recover_stale` would write to disk when restoring a previous run.
    """
    import json
    task.next_run = next_run
    with store._lock:  # noqa: SLF001 — test fixture reaches into the store
        store._conn.execute(  # noqa: SLF001
            "INSERT OR REPLACE INTO scheduled_tasks (id, enabled, next_run, data) VALUES (?, ?, ?, ?)",
            (task.id, 1 if task.enabled else 0, next_run, json.dumps(task.to_dict())),
        )
        store._conn.commit()
    return task


@pytest.mark.asyncio
async def test_stop_does_not_leave_running_status(tmp_path):
    """If the runner is mid-flight when stop() cancels it, the scheduler must
    not leave a `running` TaskRun in the store. The runner is the
    engine-side producer; the scheduler owns the overlap guard. Without
    a write from the runner, the only row in the store after stop() must
    be either (a) the last completed run the engine already persisted,
    or (b) nothing — but never a phantom `running`.
    """
    store = TaskStore(tmp_path / "tasks.db")
    task = _insert_due(store, _task(), next_run=_now() - 60)

    started = asyncio.Event()
    cancel_seen = asyncio.Event()

    async def runner(_t: ScheduledTask, _trigger: str) -> TaskRun:
        # Simulate a slow, side-effect-bearing run that gets cancelled.
        started.set()
        try:
            # Wait forever (until cancelled).
            await asyncio.Event().wait()
            run = TaskRun(
                task_id=task.id,
                started_at=_now(),
                status="ok",
                trigger=_trigger,
            )
            store.add_run(run)
            return run
        except asyncio.CancelledError:
            cancel_seen.set()
            raise

    scheduler = Scheduler(store, runner, tick_seconds=1)
    # Manually drive one tick so we control the lifecycle.
    await scheduler._tick(trigger="schedule")
    # Give the spawned task a moment to call `started.set()`.
    for _ in range(20):
        if started.is_set():
            break
        await asyncio.sleep(0.05)

    await scheduler.stop()
    assert cancel_seen.is_set(), "stop() must cancel the in-flight run"
    # No TaskRun row was added by the runner (it was cancelled before it
    # could finish), so the store must be empty — never `running`.
    runs = store.runs(task.id)
    statuses = {r.status for r in runs}
    assert "running" not in statuses, (
        f"stop() must not leave a 'running' run; saw statuses {statuses}"
    )


@pytest.mark.asyncio
async def test_catchup_fires_exactly_once_per_start(tmp_path):
    """The catch-up path is `_loop()`'s first tick with `trigger="catchup"`.
    Once the first catch-up has fired and advanced the task's next_run, a
    second catch-up in the same process must not re-run the same task.
    """
    store = TaskStore(tmp_path / "tasks.db")
    # A due task: next_run sits in the past, so the first catch-up picks it up.
    task = _insert_due(store, _task(), next_run=_now() - 60)

    run_calls: list[str] = []

    async def runner(_t: ScheduledTask, trigger: str) -> TaskRun:
        run_calls.append(trigger)
        run = TaskRun(
            task_id=task.id,
            started_at=_now(),
            status="ok",
            trigger=trigger,
        )
        store.add_run(run)
        return run

    async def _wait_for_spawned(scheduler: Scheduler) -> None:
        # The runner is spawned in _tick via asyncio.create_task and the
        # scheduler doesn't await it. Drain the spawned set on each poll.
        for _ in range(40):
            await asyncio.sleep(0.025)
            if not scheduler._spawned:  # noqa: SLF001 — test-only peek
                return
        # Anything still running is a sign the runner leaked.
        leaked = list(scheduler._spawned)  # noqa: SLF001
        if leaked:
            for t in leaked:
                t.cancel()
            pytest.fail("scheduler did not drain in-flight runs in time")

    scheduler = Scheduler(store, runner, tick_seconds=10)
    # First catch-up tick: the due task fires.
    await scheduler._tick(trigger="catchup")
    await _wait_for_spawned(scheduler)
    assert run_calls.count("catchup") == 1, (
        f"catch-up should fire on the first tick; got {run_calls!r}"
    )
    # Second catch-up: next_run was advanced, so nothing to do.
    await scheduler._tick(trigger="catchup")
    await _wait_for_spawned(scheduler)
    assert run_calls.count("catchup") == 1, (
        f"catch-up should not double-fire; got {run_calls!r}"
    )

    # The task's run_count was advanced by the runner via the store.
    refreshed = store.get(task.id)
    assert refreshed is not None
    assert refreshed.run_count == 1
    assert refreshed.next_run is not None
    # The next_run was advanced past the original due time.
    assert refreshed.next_run > _now() - 60


@pytest.mark.asyncio
async def test_committed_side_effect_not_replayed_on_restart(tmp_path):
    """If a runner committed a side effect (the engine wrote the idemlog),
    then stop() cancels the run before the TaskRun row is appended, a
    subsequent scheduler instance must not run the same task again until
    its next scheduled fire. The Reliable Task Runtime (idemlog) is the
    real defense; here we just confirm the scheduler doesn't double-fire
    on a same-process restart.
    """
    store = TaskStore(tmp_path / "tasks.db")
    task = _insert_due(store, _task(), next_run=_now() - 60)

    run_count = 0

    async def runner(_t: ScheduledTask, _trigger: str) -> TaskRun:
        nonlocal run_count
        run_count += 1
        run = TaskRun(
            task_id=task.id,
            started_at=_now(),
            status="ok",
            trigger=_trigger,
        )
        store.add_run(run)
        return run

    async def _wait_for_spawned(scheduler: Scheduler) -> None:
        for _ in range(40):
            await asyncio.sleep(0.025)
            if not scheduler._spawned:  # noqa: SLF001
                return
        leaked = list(scheduler._spawned)  # noqa: SLF001
        for t in leaked:
            t.cancel()

    scheduler1 = Scheduler(store, runner, tick_seconds=10)
    await scheduler1._tick(trigger="catchup")
    await _wait_for_spawned(scheduler1)
    assert run_count == 1
    # Build a fresh scheduler pointing at the same store.
    scheduler2 = Scheduler(store, runner, tick_seconds=10)
    await scheduler2._tick(trigger="catchup")
    await _wait_for_spawned(scheduler2)
    # The task was already advanced (next_run is in the future), so a
    # second catch-up doesn't re-run it.
    assert run_count == 1, "scheduler must not re-run a task whose next_run advanced"
