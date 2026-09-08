"""Rust-authoritative TaskStore facade.
 
ADR-024 R1 Task Identity Hard-Cut: Rust ``delta_core`` is the sole authority for
all task identity writes and reads. Python retains only the public adapter used
by the current runtime and the stable value types from the v0.3.2 contract.
 
There is deliberately no Python SQLite writer and no runtime fallback. A
missing, incompatible, or failed Rust Core process raises ``DeltaCoreError``.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.automation.models import ScheduledTask, TaskRun
from packages.delta_core_client import DeltaCoreError, default_client


def _epoch_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _tz(name: str):
    """Resolve a schedule timezone to a DST-aware tzinfo, or None for the machine's local
    zone. None (not a fixed-offset tzinfo) is deliberate: naive datetimes let .timestamp()/
    the C library apply local DST at the fire date. A frozen `datetime.now().astimezone()`
    offset baked in whatever offset was in effect at compute time and misfired across a DST
    boundary. An unknown IANA name falls back to local (None) rather than raising."""
    if not name or name.lower() == "local":
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


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


class TaskStore:
    """Thin Python API over the single Rust task identity authority.
 
    Every method delegates to ``delta_core`` via the process-wide
    :func:`~packages.delta_core_client.default_client`. The SQLite
    database is opened and managed entirely by the Rust side; Python
    never touches it directly.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(self.path)
        # Process-local lock coordinates calls; it does not protect a Python
        # database connection (there isn't one).
        self._lock = threading.RLock()
        # Schema creation (CREATE TABLE IF NOT EXISTS) is handled by the
        # Rust TaskStore::open on first command — no Python init needed.

    def _invoke(self, cmd: str, **kwargs: Any) -> Any:
        """Send one command to delta_core and return the result."""
        return default_client().command({"cmd": cmd, "db": self._db_path, **kwargs})

    # -- tasks ------------------------------------------------------------------

    def save(self, task: ScheduledTask) -> ScheduledTask:
        """Save a task, computing next_run and updated_at."""
        task.updated_at = _epoch_now()
        task.next_run = compute_next_run(task) if task.enabled else None
        data = task.to_dict()
        data_str = json.dumps(data)
        self._invoke(
            "task.save",
            task_id=task.id,
            enabled=task.enabled,
            next_run=task.next_run,
            data=data_str,
        )
        return task

    def get(self, task_id: str) -> ScheduledTask | None:
        result = self._invoke("task.get", task_id=task_id)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise DeltaCoreError("invalid task.get response")
        import json
        return ScheduledTask.from_dict(json.loads(result["data"]))

    def list(self) -> list[ScheduledTask]:
        result = self._invoke("task.list")
        if not isinstance(result, dict) or "tasks" not in result:
            raise DeltaCoreError("invalid task.list response")
        import json
        return [ScheduledTask.from_dict(json.loads(t["data"])) for t in result["tasks"]]

    def delete(self, task_id: str) -> bool:
        result = self._invoke("task.delete", task_id=task_id)
        if not isinstance(result, dict):
            raise DeltaCoreError("invalid task.delete response")
        return True

    def due(self, *, now: float | None = None) -> list[ScheduledTask]:
        now = now if now is not None else _epoch_now()
        result = self._invoke("task.due", now=now)
        if not isinstance(result, dict) or "tasks" not in result:
            raise DeltaCoreError("invalid task.due response")
        import json
        return [ScheduledTask.from_dict(json.loads(t["data"])) for t in result["tasks"]]

    # -- runs -------------------------------------------------------------------

    def add_run(self, run: TaskRun) -> TaskRun:
        import json
        data_str = json.dumps(run.to_dict())
        self._invoke(
            "task.add_run",
            run_id=run.run_id,
            task_id=run.task_id,
            started_at=run.started_at,
            data=data_str,
            workspace=run.workspace or "",
        )
        return run

    def find_run(self, run_id: str) -> TaskRun | None:
        result = self._invoke("task.find_run", run_id=run_id)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise DeltaCoreError("invalid task.find_run response")
        import json
        return TaskRun.from_dict(json.loads(result["data"]))

    def task_for_run_session(self, session_id: str) -> ScheduledTask | None:
        """The owning task of a run session ('__run__<run_id>'), or None. How standing
        scoped approvals resolve which automation a live approval belongs to (§25)."""
        if not session_id.startswith("__run__"):
            return None
        result = self._invoke("task.task_for_run_session", session_id=session_id)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise DeltaCoreError("invalid task.task_for_run_session response")
        import json
        return ScheduledTask.from_dict(json.loads(result["data"]))

    def runs(self, task_id: str, *, limit: int = 50) -> list[TaskRun]:
        result = self._invoke("task.runs", task_id=task_id, limit=limit)
        if not isinstance(result, dict) or "runs" not in result:
            raise DeltaCoreError("invalid task.runs response")
        import json
        return [TaskRun.from_dict(json.loads(r["data"])) for r in result["runs"]]

    def close(self) -> None:
        """Release the SQLite handle for this store in the Rust ConnCache.
 
        Idempotent: calling multiple times is safe. After close, subsequent
        operations on this instance will reopen the handle transparently.
        """
        self._invoke("task.close")