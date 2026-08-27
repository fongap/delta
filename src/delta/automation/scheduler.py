"""The scheduler loop — runs in the always-on server.

Policy (agreed): **run-once-catch-up** for runs missed while down (due tasks fire once on
startup, then resume), and **skip-on-overlap** (don't stack a run if the previous is still
going). The actual execution is injected as `runner(task, trigger) -> TaskRun` so this stays
independent of the engine/manager.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .models import ScheduledTask, TaskRun
from .store import TaskStore

logger = logging.getLogger("delta.automation")

Runner = Callable[[ScheduledTask, str], Awaitable[TaskRun]]


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        runner: Runner,
        *,
        tick_seconds: float = 30.0,
        extra_tick: Callable[[], Awaitable[None]] | None = None,
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

    async def _execute(self, task: ScheduledTask, *, trigger: str) -> TaskRun | None:
        try:
            run = await self.runner(task, trigger)
        except Exception as exc:
            logger.exception("task %s run failed", task.id)
            run = TaskRun(
                task_id=task.id, status="error", error=str(exc), trigger=trigger
            )
            self.store.add_run(run)
        # advance the task (run_count/last_run) → save recomputes next_run.
        fresh = self.store.get(task.id)
        if fresh is not None:
            fresh.run_count += 1
            fresh.last_run = run.started_at if run else None
            # A runner may legitimately return None ("nothing to do") — that is not an
            # error, so record it distinctly instead of the indistinguishable "error".
            fresh.last_status = run.status if run else "skipped"
            self.store.save(fresh)
        return run
