"""TaskStore delegate to Rust Core when is_rust_authority is active.

This module is the R1 Task identity authority switch (ADR-016).
When ``DELTA_RUST_AUTHORITY=1`` is set and the binary is built,
write calls (``save``, ``delete``, ``add_run``) on
:class:`TaskStoreWithDelegate` are forwarded to the Rust
``write_tasks`` binary instead of writing through the Python
SQLite connection. The Python code path is otherwise unchanged.

The default factory :func:`maybe_wrap_taskstore` returns either a
plain :class:`TaskStore` (when authority is not active) or a
delegate wrapper (when authority is active).

Risk: this PR introduces a real authority switch. Rolling back is
achieved by either (a) unsetting ``DELTA_RUST_AUTHORITY`` or (b)
uninstalling the ``delta-runtime-native`` binary — both routes
immediately restore Python-only writes. The Python read path is
unaffected; both authorities write to the same SQLite tables.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.automation.store import TaskStore

from packages.storage_authority import is_rust_authority

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"


def _binary_path() -> Path | None:
    target = "write_tasks.exe" if sys.platform == "win32" else "write_tasks"
    path = CRATE_DIR / "target" / "debug" / target
    return path if path.exists() else None


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the binary is available."""
    if not is_rust_authority("task_identity"):
        return False
    return _binary_path() is not None


def _invoke(action: str, db_path: str, **kw: Any) -> None:
    binary = _binary_path()
    if binary is None:
        raise RuntimeError("write_tasks binary not built")
    args = [str(binary), "--db", db_path, "--action", action]
    for key, value in kw.items():
        if value is None:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, (dict, list)):
            value = json.dumps(value)
        args += [flag, str(value)]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"write_tasks {action} failed: rc={result.returncode}, stderr={result.stderr}"
        )


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
            _invoke(
                "save_task",
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
            _invoke("delete_task", self._db_path, task_id=task_id)
            return True
        return self._inner.delete(task_id)

    def add_run(self, run: Any) -> Any:
        if self._delegate:
            _invoke(
                "add_run",
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
    """Return a delegate wrapper iff Rust authority is active; else the original."""
    if not _is_delegate_active():
        return store
    return TaskStoreWithDelegate(store)
