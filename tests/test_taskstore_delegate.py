"""TaskStoreWithDelegate — R1.5 Task identity authority cutover behavior.

R1.5 Task identity authority cutover (ADR-016). The
``TaskStoreWithDelegate`` wrapper is the entry point for opt-in
Rust Core writes via the unified ``delta_core`` process. The default
``TaskStore`` keeps its current behavior; this wrapper activates
only when ``DELTA_RUST_AUTHORITY=task_identity`` is set and the
``delta_core`` binary is built.

Fail-closed (P0-3): when authority is declared but the binary is
missing, ``maybe_wrap_taskstore`` raises ``DeltaCoreError`` instead
of falling back to Python.
"""

from __future__ import annotations

import gc
import sys
from pathlib import Path

import pytest

from core.automation.store import TaskStore
from core.automation.store_delegate import (
    TaskStoreWithDelegate,
    maybe_wrap_taskstore,
)
from packages.delta_core_client import DeltaCoreError

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / ("delta_core.exe" if sys.platform == "win32" else "delta_core")


@pytest.fixture
def rust_authority_off(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)


@pytest.fixture
def rust_authority_on(monkeypatch):
    if not BINARY.exists():
        pytest.skip("delta_core binary not built")
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "task_identity")


def _make_task_dict(task_id: str, **kw) -> dict:
    d = {
        "id": task_id,
        "title": kw.get("title", "Test"),
        "instructions": kw.get("instructions", ""),
        "workspace": kw.get("workspace", ""),
        "agent": kw.get("agent", "code"),
        "enabled": kw.get("enabled", True),
        "schedule": kw.get("schedule", {"kind": "cron", "cron": "0 * * * *"}),
        "run_count": kw.get("run_count", 0),
        "max_runs": kw.get("max_runs"),
        "updated_at": kw.get("updated_at", 0.0),
        "next_run": kw.get("next_run"),
    }
    return d


def test_maybe_wrap_returns_plain_store_when_authority_off(rust_authority_off, tmp_path):
    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    wrapped = maybe_wrap_taskstore(store)
    assert wrapped is store
    assert not isinstance(wrapped, TaskStoreWithDelegate)
    store.close()


def test_maybe_wrap_returns_delegate_when_authority_on(rust_authority_on, tmp_path):
    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    wrapped = maybe_wrap_taskstore(store)
    assert isinstance(wrapped, TaskStoreWithDelegate)
    assert wrapped.delegate_active is True
    wrapped.close()


def test_maybe_wrap_fails_closed_when_authority_on_but_binary_missing(monkeypatch, tmp_path):
    """P0-3: when authority is declared but delta_core is unavailable,
    maybe_wrap_taskstore must raise DeltaCoreError, not fall back."""
    from packages.delta_core_client import DeltaCoreClient

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "task_identity")
    monkeypatch.setattr(DeltaCoreClient, "_find_binary", staticmethod(lambda: None))
    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    with pytest.raises(DeltaCoreError, match="fail-closed"):
        maybe_wrap_taskstore(store)
    store.close()


def test_delegate_writes_task_via_rust(rust_authority_on, tmp_path):
    from core.automation.models import ScheduledTask

    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    wrapped = maybe_wrap_taskstore(store)

    task = ScheduledTask.from_dict(_make_task_dict("run_d1", title="Delegate Test"))
    wrapped.save(task)

    result = wrapped.get("run_d1")
    assert result is not None
    assert result.id == "run_d1"
    assert result.title == "Delegate Test"

    wrapped.close()
    del wrapped
    gc.collect()


def test_delegate_writes_run_via_rust(rust_authority_on, tmp_path):
    from core.automation.models import ScheduledTask, TaskRun

    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    wrapped = maybe_wrap_taskstore(store)

    task = ScheduledTask.from_dict(_make_task_dict("task_d2"))
    wrapped.save(task)

    run = TaskRun(
        run_id="run_d2",
        task_id="task_d2",
        started_at=1000.0,
        workspace="ws_d2",
    )
    wrapped.add_run(run)

    found = wrapped.find_run("run_d2")
    assert found is not None
    assert found.task_id == "task_d2"

    wrapped.close()
    del wrapped
    gc.collect()


def test_delegate_deletes_task(rust_authority_on, tmp_path):
    from core.automation.models import ScheduledTask

    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    wrapped = maybe_wrap_taskstore(store)

    task = ScheduledTask.from_dict(_make_task_dict("task_d3"))
    wrapped.save(task)
    assert wrapped.get("task_d3") is not None

    wrapped.delete("task_d3")
    assert wrapped.get("task_d3") is None

    wrapped.close()
    del wrapped
    gc.collect()


def test_plain_store_path_unchanged_when_authority_off(rust_authority_off, tmp_path):
    from core.automation.models import ScheduledTask

    db = tmp_path / "tasks.db"
    store = TaskStore(db)

    task = ScheduledTask.from_dict(_make_task_dict("task_d4", title="Plain"))
    store.save(task)

    found = store.get("task_d4")
    assert found is not None
    assert found.title == "Plain"

    store.close()
    del store
    gc.collect()
