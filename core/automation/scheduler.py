"""The scheduler loop — runs in the always-on server.

Policy (agreed): **run-once-catch-up** for runs missed while down (due tasks fire once on
startup, then resume), and **skip-on-overlap** (don't stack a run if the previous is still
going). The actual execution is injected as `runner(task, trigger) -> TaskRun` so this stays
independent of the engine/manager.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from core.automation.models import ScheduledTask, TaskRun
from core.automation.store import TaskStore

logger = logging.getLogger("core.automation")

Runner = Callable[[ScheduledTask, str], Awaitable[TaskRun]]


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        runner: Runner,
        *,
        tick_seconds: float = 30.0,
        extra_tick: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.tick_seconds = tick_seconds
        # An extra per-tick coroutine (self-wake resumption: resume sessions whose wakes are due).
        self.extra_tick = extra_tick
        self._task: asyncio.Task | None = None
        self._running_ids: set[str] = set()  # overlap guard
        self._spawned: set[asyncio.Task] = set()  # keep spawned runs referenced

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # In-flight runs died with the loop before they were spawned; keep that shutdown
        # contract now that they're independent tasks (a suspended run must not outlive us).
        for spawned in list(self._spawned):
            spawned.cancel()
            try:
                await spawned
            except asyncio.CancelledError:
                pass
        self._spawned.clear()

    async def _loop(self) -> None:
        # First pass = run-once-catch-up for anything missed while the server was down.
        try:
            await self._tick(trigger="catchup")
        except Exception:
            logger.exception("scheduler catch-up failed")
        while True:
            await asyncio.sleep(self.tick_seconds)
            try:
                await self._tick(trigger="schedule")
            except Exception:
                logger.exception("scheduler tick failed")

    async def _tick(self, *, trigger: str) -> None:
        for task in self.store.due():
            # Spawn, don't await: a run can suspend on a parked approval (standing
            # scoped approvals, §25) and one blocked automation must never stall the
            # scheduler loop, other due tasks, or self-wake resumption.
            #
            # Claim the overlap guard HERE, synchronously before spawning. A tick can
            # read a stale due row while the task's previous run is still finishing
            # (its next_run save lands after this snapshot); claiming before the spawn
            # makes that duplicate skip instead of racing past the released guard and
            # executing twice. The claim is released by _run_claimed's finally.
            if task.id in self._running_ids:
                logger.info("skipping %s — previous run still going", task.id)
                continue
            self._running_ids.add(task.id)
            spawned = asyncio.create_task(self._run_claimed(task, trigger=trigger))
            self._spawned.add(spawned)
            spawned.add_done_callback(self._spawned.discard)
        if self.extra_tick is not None:
            try:
                await self.extra_tick()
            except Exception:
                logger.exception("scheduler extra_tick (wake resume) failed")

    async def _run_claimed(self, task: ScheduledTask, *, trigger: str) -> TaskRun | None:
        """Execute a task whose overlap guard was already claimed by _tick."""
        try:
            return await self._execute(task, trigger=trigger)
        finally:
            self._running_ids.discard(task.id)

    async def run_task(self, task: ScheduledTask, *, trigger: str) -> TaskRun | None:
        if task.id in self._running_ids:  # skip-on-overlap
            logger.info("skipping %s — previous run still going", task.id)
            return None
        self._running_ids.add(task.id)
        try:
            return await self._execute(task, trigger=trigger)
        finally:
            self._running_ids.discard(task.id)

    @staticmethod
    def _project_next_run(task: ScheduledTask, *, after: float | None = None) -> float | None:
        """Compute the post-completion next_run using the projected state.

        AF-01/AF-02: the scheduler is the single finalize owner. It projects
        run_count+1 to determine exhaustion, then (if not exhausted) computes
        next_run via the pure `compute_next_run` and hands it to `complete_run`
        so Rust persists JSON and SQL columns atomically in one transaction.
        """
        from core.automation.store import compute_next_run

        projected = task
        projected.run_count = task.run_count + 1
        if task.max_runs and projected.run_count >= task.max_runs:
            return None
        return compute_next_run(projected, after=after)

    async def _execute(self, task: ScheduledTask, *, trigger: str) -> TaskRun | None:
        """Execute one task run. The scheduler is the single completion owner.

        AF-01 fix: the runner only executes and returns a ``TaskRun``. It must not
        call ``complete_run`` or mutate task stats (run_count/last_run/last_status/
        enabled/next_run). This scheduler alone finalizes — once — via
        ``TaskStore.complete_run``, on both the success and the failure path.
        """
        try:
            run = await self.runner(task, trigger)
        except Exception as exc:
            logger.exception("task %s run failed", task.id)
            run = TaskRun(
                task_id=task.id, status="error", error=str(exc), trigger=trigger
            )
            self.store.add_run(run)
        if run is not None:
            # Single finalize path — the runner never calls complete_run itself.
            # Guarantee finished_at is a real timestamp: a runner returning a
            # bare TaskRun (or the scheduler's own error path) may leave it None.
            if run.finished_at is None:
                run.finished_at = run.started_at
            next_run = self._project_next_run(task)
            self.store.complete_run(run, run.finished_at, next_run=next_run)
        return run
