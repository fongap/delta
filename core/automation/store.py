"""Thin Rust-authoritative facade for scheduled tasks + run history.

After the R1 Task Identity Hard-Cut (ADR-024), Rust ``delta_core``
is the sole authority for the ``task_identity`` domain.  This module
delegates all persistence to Rust via :class:`DeltaCoreClient` and
contains **no** SQLite writer, fallback path, or authority selector.

``compute_next_run()`` remains in Python (R4 domain — scheduler
computes next fire times locally).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.automation.models import ScheduledTask, TaskRun
from packages.delta_core_client import DeltaCoreClient, DeltaCoreError


def compute_next_run(
    task: ScheduledTask, *, after: float | None = None
) -> float | None:
    """Next fire time (epoch seconds), or None if the task is exhausted/one-shot-past.

    P3 §7.3 §734: a task with a ``trigger`` (event-driven) has no
    next_run — it's dispatched on events, not by the clock. We
    return None so the scheduler's ``due()`` query skips it (the
    TriggerRegistry's dispatch path is what fires it instead).
    """
    if getattr(task, "trigger", None) is not None:
        return None
    sched = task.schedule
    now = after if after is not None else _epoch_now()
    if sched.kind == "once":
        if not sched.fire_at:
            return None
        try:
            dt = datetime.fromisoformat(sched.fire_at)
        except ValueError:
            return None
        tz = _tz(sched.timezone)
        if dt.tzinfo is None and tz is not None:
            dt = dt.replace(tzinfo=tz)
        # Naive local dt: datetime.timestamp() interprets it in the machine's zone and is
        # DST-aware for the actual fire DATE (via the C library), so a "once" task set in
        # summer for a winter date fires at the right wall-clock instead of an hour off.
        ts = dt.timestamp()
        return ts if (task.run_count == 0 and ts > now) else None
    # cron
    from croniter import croniter

    if not sched.cron or not croniter.is_valid(sched.cron):
        return None
    if task.max_runs is not None and task.run_count >= task.max_runs:
        return None
    tz = _tz(sched.timezone)
    # Local: a naive base makes croniter compute in local wall-clock and .timestamp() apply
    # the correct DST offset per occurrence. A named zone anchors the base in that zone.
    base = datetime.fromtimestamp(now) if tz is None else datetime.fromtimestamp(now, tz=tz)
    return croniter(sched.cron, base).get_next(datetime).timestamp()


def _tz(name: str):
    """Resolve a schedule timezone to a DST-aware tzinfo, or None for the machine's local
    zone. None (not a fixed-offset tzinfo) is deliberate: naive datetimes let .timestamp()/
    the C library apply local DST at the fire date. A frozen `datetime.now().astimezone()`
    offset baked in whatever offset was in effect at compute time and misfired across a DST
    boundary. An unknown IANA name falls back to local (None) rather than raising."""
    if not name or name.lower() == "local":
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def _epoch_now() -> float:
    return datetime.now(timezone.utc).timestamp()


class TaskStore:
    """Rust-authoritative task store facade.

    All read/write operations are delegated to ``delta_core`` via
    :class:`DeltaCoreClient`.  There is no Python SQLite writer,
    fallback path, or authority selector.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._client = DeltaCoreClient()

    # -- helpers ----------------------------------------------------------------

    def _invoke(self, cmd: str, **kwargs: Any) -> Any:
        """Send a command to delta_core and return the result.

        Raises :class:`DeltaCoreError` on any failure (fail-closed).
        """
        payload = {"cmd": cmd, "db": self.path, **kwargs}
        return self._client.command(payload)

    @staticmethod
    def _validate_dict(result: Any, cmd: str) -> dict:
        """Validate that result is a dict; raise DeltaCoreError otherwise."""
        if not isinstance(result, dict):
            raise DeltaCoreError(f"invalid {cmd} response: not a dict")
        return result

    @staticmethod
    def _validate_list(result: Any, cmd: str) -> list:
        """Validate that result is a list; raise DeltaCoreError otherwise."""
        if not isinstance(result, list):
            raise DeltaCoreError(f"invalid {cmd} response: not a list")
        return result

    # -- tasks ------------------------------------------------------------------

    def save(self, task: ScheduledTask) -> ScheduledTask:
        task.updated_at = _epoch_now()
        task.next_run = compute_next_run(task) if task.enabled else None
        self._invoke(
            "task.save",
            task_id=task.id,
            enabled=task.enabled,
            next_run=task.next_run,
            data=json.dumps(task.to_dict()),
        )
        return task

    def get(self, task_id: str) -> ScheduledTask | None:
        result = self._invoke("task.get", task_id=task_id)
        if result is None:
            return None
        self._validate_dict(result, "task.get")
        return ScheduledTask.from_dict(result)

    def list(self) -> list[ScheduledTask]:
        result = self._invoke("task.list")
        items = self._validate_list(result, "task.list")
        return [ScheduledTask.from_dict(item) for item in items]

    def delete(self, task_id: str) -> bool:
        result = self._invoke("task.delete", task_id=task_id)
        self._validate_dict(result, "task.delete")
        deleted = result.get("deleted")
        if not isinstance(deleted, bool):
            raise DeltaCoreError("invalid task.delete response: missing 'deleted' bool")
        return deleted

    def due(self, *, now: float | None = None) -> list[ScheduledTask]:
        now = now if now is not None else _epoch_now()
        result = self._invoke("task.due", now=now)
        items = self._validate_list(result, "task.due")
        return [ScheduledTask.from_dict(item) for item in items]

    # -- runs -------------------------------------------------------------------

    def add_run(self, run: TaskRun) -> TaskRun:
        self._invoke(
            "task.add_run",
            run_id=run.run_id,
            task_id=run.task_id,
            started_at=run.started_at,
            data=json.dumps(run.to_dict()),
            workspace=run.workspace or "",
        )
        return run

    def find_run(self, run_id: str) -> TaskRun | None:
        result = self._invoke("task.find_run", run_id=run_id)
        if result is None:
            return None
        self._validate_dict(result, "task.find_run")
        return TaskRun.from_dict(result)

    def task_for_run_session(self, session_id: str) -> ScheduledTask | None:
        """The owning task of a run session ('__run__<run_id>'), or None. How standing
        scoped approvals resolve which automation a live approval belongs to (§25)."""
        if not session_id.startswith("__run__"):
            return None
        result = self._invoke("task.task_for_run_session", session_id=session_id)
        if result is None:
            return None
        self._validate_dict(result, "task.task_for_run_session")
        return ScheduledTask.from_dict(result)

    def runs(self, task_id: str, *, limit: int = 50) -> list[TaskRun]:
        result = self._invoke("task.runs", task_id=task_id, limit=limit)
        items = self._validate_list(result, "task.runs")
        return [TaskRun.from_dict(item) for item in items]

    def close(self) -> None:
        self._invoke("task.close")
