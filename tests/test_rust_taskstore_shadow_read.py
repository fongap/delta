"""Cross-language consistency: Python writes tasks.db → Rust inspect_tasks reads it.

R1 Pre-R1 plumbing proof (ADR-012). The Python TaskStore writes
scheduled tasks and run history to a SQLite DB. The Rust TaskStoreReader
opens the same DB read-only and reports the same data.

This test builds the Rust ``inspect_tasks`` binary on first run and
caches it under ``target/``. If the Rust toolchain is unavailable, the
test skips (not fail) — it is a cross-language integration test, not a
Python unit test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from core.automation.models import ScheduledTask, Schedule, TaskRun
from core.automation.store import TaskStore

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / "inspect_tasks"
BINARY_EXE = CRATE_DIR / "target" / "debug" / "inspect_tasks.exe"


def _cargo_available() -> bool:
    return shutil.which("cargo") is not None


def _build_binary() -> Path | None:
    target = BINARY_EXE if sys.platform == "win32" else BINARY
    if target.exists():
        return target
    if not _cargo_available():
        return None
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(CRATE_DIR / "target")
    result = subprocess.run(
        ["cargo", "build", "--bin", "inspect_tasks"],
        cwd=CRATE_DIR,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        return None
    return target if target.exists() else None


@pytest.fixture(scope="module")
def rust_binary():
    binary = _build_binary()
    if binary is None:
        pytest.skip("Rust toolchain or inspect_tasks binary not available")
    return binary


def _run(binary: Path, db: str, *extra: str) -> list[dict] | dict:
    result = subprocess.run(
        [str(binary), "--db", db, *extra],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"inspect_tasks failed: {result.stderr}"
    return json.loads(result.stdout)


def _make_task(task_id: str, title: str) -> ScheduledTask:
    return ScheduledTask(
        title=title,
        instructions="hello",
        schedule=Schedule(kind="cron", cron="0 0 * * *"),
        workspace="/tmp/ws",
        id=task_id,
        agent="delta",
    )


def test_python_writes_rust_reads_task_list(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        store = TaskStore(db)

        store.save(_make_task("task_a", "first"))
        store.save(_make_task("task_b", "second"))
        store.save(_make_task("task_c", "third"))
        store.close()

        result = _run(rust_binary, str(db), "--mode", "tasks")
        assert isinstance(result, list)
        ids = {t["id"] for t in result}
        assert ids == {"task_a", "task_b", "task_c"}


def test_python_writes_rust_reads_task_runs(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        store = TaskStore(db)

        store.save(_make_task("task_x", "x"))
        store.add_run(TaskRun(run_id="run_1", task_id="task_x", started_at=100.0, status="ok"))
        store.add_run(TaskRun(run_id="run_2", task_id="task_x", started_at=200.0, status="error"))
        store.add_run(TaskRun(run_id="run_3", task_id="task_x", started_at=300.0, status="ok"))
        store.close()

        result = _run(rust_binary, str(db), "--mode", "runs", "--task-id", "task_x")
        assert isinstance(result, list)
        run_ids = [r["run_id"] for r in result]
        assert run_ids == ["run_3", "run_2", "run_1"]


def test_python_writes_rust_reads_specific_run(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        store = TaskStore(db)

        store.save(_make_task("task_y", "y"))
        store.add_run(TaskRun(run_id="run_z", task_id="task_y", started_at=999.0, status="ok"))
        store.close()

        result = _run(rust_binary, str(db), "--mode", "run", "--task-id", "run_z")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["run_id"] == "run_z"
        assert result[0]["task_id"] == "task_y"


def test_rust_returns_empty_for_missing_task(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        store = TaskStore(db)

        store.save(_make_task("task_a", "a"))
        store.close()

        result = _run(rust_binary, str(db), "--mode", "runs", "--task-id", "task_nonexistent")
        assert result == []


def test_rust_returns_empty_for_missing_run(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        store = TaskStore(db)

        store.save(_make_task("task_a", "a"))
        store.close()

        result = _run(rust_binary, str(db), "--mode", "run", "--task-id", "run_missing")
        assert result == []
