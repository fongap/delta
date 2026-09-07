"""TaskStore delegate to Rust Core when is_rust_authority is active.

This module is the R1.5 Task identity authority cutover (ADR-016).
When ``DELTA_RUST_AUTHORITY=task_identity`` is set, write calls
(``save``, ``delete``, ``add_run``) on
:class:`TaskStoreWithDelegate` are forwarded to the unified
``delta_core`` Rust process via :class:`DeltaCoreClient` instead of
writing through the Python SQLite connection.

R1.5 cutover: the delegate now uses the persistent
:class:`DeltaCoreClient` (NDJSON over stdin/stdout) instead of
spawning a fresh ``write_tasks`` subprocess per command. The
per-op CLI binary ``write_tasks`` is retained only as a diagnostic
tool.

Fail-closed (P0-3): when ``DELTA_RUST_AUTHORITY=task_identity`` is
declared but the ``delta_core`` binary is unavailable, the delegate
raises :class:`DeltaCoreError` — it never silently falls back to
the Python write path.
"""

from __future__ import annotations

import json
from typing import Any

from core.automation.store import TaskStore

from packages.delta_core_client import DeltaCoreClient, DeltaCoreError, default_client
from packages.storage_authority import is_rust_authority


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the delta_core binary is available."""
    if not is_rust_authority("task_identity"):
        return False
    return DeltaCoreClient._find_binary() is not None


def _invoke_core(cmd: str, db_path: str, **kw: Any) -> Any:
    """Send one command to delta_core via the shared DeltaCoreClient."""
    client = default_client()
    return client.command({"cmd": cmd, "db": db_path, **kw})


class TaskStoreWithDelegate:
    """Wrap a :class:`TaskStore` and forward writes to Rust.

    Read methods (:meth:`get`, :meth:`list`, :meth:`due`,
    :meth:`find_run`, :meth:`runs`) call through to the inner
    Python object. Write methods (:meth:`save`, :meth:`delete`,
    :meth:`add_run`) check :func:`is_rust_authority` and either
    delegate to Rust or call the Python implementation.

    Use :func:`maybe_wrap_taskstore` to construct.
    """

    def __init__(self, inner: TaskStore):
        self._inner = inner
        self._db_path = inner.path
        self._delegate = _is_delegate_active()

    @property
    def delegate_active(self) -> bool:
        return self._delegate

    # -- writes ----------------------------------------------------------------

    def save(self, task: Any) -> Any:
        if self._delegate:
            _invoke_core(
                "task.save",
                self._db_path,
                task_id=task.id,
                enabled=task.enabled,
                next_run=task.next_run if task.next_run is not None else None,
                data=json.dumps(task.to_dict()),
            )
            return task
        return self._inner.save(task)

    def delete(self, task_id: str) -> bool:
        if self._delegate:
            _invoke_core("task.delete", self._db_path, task_id=task_id)
            return True
        return self._inner.delete(task_id)

    def add_run(self, run: Any) -> Any:
        if self._delegate:
            _invoke_core(
                "task.add_run",
                self._db_path,
                run_id=run.run_id,
                task_id=run.task_id,
                started_at=run.started_at,
                data=json.dumps(run.to_dict()),
                workspace=run.workspace or "",
            )
            return run
        return self._inner.add_run(run)

    # -- reads / lifecycle: forward to inner -----------------------------------

    def get(self, task_id: str) -> Any:
        return self._inner.get(task_id)

    def list(self) -> list[Any]:
        return self._inner.list()

    def due(self, *, now: float | None = None) -> list[Any]:
        return self._inner.due(now=now)

    def find_run(self, run_id: str) -> Any:
        return self._inner.find_run(run_id)

    def task_for_run_session(self, session_id: str) -> Any:
        return self._inner.task_for_run_session(session_id)

    def runs(self, task_id: str, *, limit: int = 50) -> list[Any]:
        return self._inner.runs(task_id, limit=limit)

    def close(self) -> None:
        self._inner.close()


def maybe_wrap_taskstore(store: TaskStore) -> TaskStore | TaskStoreWithDelegate:
    """Return a delegate wrapper iff Rust authority is active; else the original.

    Fail-closed (P0-3): when ``DELTA_RUST_AUTHORITY`` declares
    ``task_identity`` as a Rust domain but the ``delta_core`` binary
    is unavailable, this raises :class:`DeltaCoreError` rather than
    silently falling back to the Python write path.
    """
    if not is_rust_authority("task_identity"):
        return store
    if not _is_delegate_active():
        raise DeltaCoreError(
            "DELTA_RUST_AUTHORITY declares task_identity as a Rust domain "
            "but delta_core binary is not available; refusing to fall "
            "back to Python (fail-closed). Build delta_core or unset "
            "DELTA_RUST_AUTHORITY."
        )
    return TaskStoreWithDelegate(store)
