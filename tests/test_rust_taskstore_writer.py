"""Cross-language consistency: Rust writes task store → Python reads them.

R1 Task identity authority switch (ADR-016). The Rust TaskStoreWriter
writes scheduled tasks and task runs to a SQLite DB. The Python
TaskStore reads the same DB and must see identical data.

This test builds the Rust ``write_tasks`` binary on first run. If
the Rust toolchain is unavailable, the test skips.
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / "write_tasks"
BINARY_EXE = CRATE_DIR / "target" / "debug" / "write_tasks.exe"


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
        ["cargo", "build", "--bin", "write_tasks"],
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
        pytest.skip("Rust toolchain or write_tasks binary not available")
    return binary


def _run(binary: Path, db: str, action: str, **kw) -> int:
    args = [str(binary), "--db", db, "--action", action]
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
    assert result.returncode == 0, f"write_tasks failed: {result.stderr}"
    return result.returncode


def _make_task_dict(task_id: str, title: str = "Test", **kw) -> dict:
    """Build a full ScheduledTask.to_dict()-compatible JSON blob."""
    d = {
        "id": task_id,
        "title": title,
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


def test_rust_writes_task_python_reads(rust_binary):
    from core.automation.store import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        data = _make_task_dict("task_a", title="Test", agent="code")
        _run(
            rust_binary,
            str(db),
            "save_task",
            task_id="task_a",
            enabled=True,
            next_run=1000.0,
            data=data,
        )

        store = TaskStore(db)
        try:
            task = store.get("task_a")
            assert task is not None
            assert task.id == "task_a"
            assert task.title == "Test"
        finally:
            store.close()
            del store
            gc.collect()


def test_rust_writes_run_python_reads(rust_binary):
    from core.automation.store import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        data = _make_task_dict("task_b", title="Test B")
        _run(
            rust_binary,
            str(db),
            "save_task",
            task_id="task_b",
            enabled=True,
            data=data,
        )
        _run(
            rust_binary,
            str(db),
            "add_run",
            run_id="run_b1",
            task_id="task_b",
            started_at=1000.0,
            data={"run_id": "run_b1", "status": "completed", "task_id": "task_b"},
            workspace="ws_1",
        )

        store = TaskStore(db)
        try:
            run = store.find_run("run_b1")
            assert run is not None
            assert run.task_id == "task_b"
            assert run.run_id == "run_b1"
        finally:
            store.close()
            del store
            gc.collect()


def test_rust_delete_task_cascades(rust_binary):
    from core.automation.store import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        _run(
            rust_binary,
            str(db),
            "save_task",
            task_id="task_c",
            enabled=True,
            data=_make_task_dict("task_c"),
        )
        _run(
            rust_binary,
            str(db),
            "add_run",
            run_id="run_c1",
            task_id="task_c",
            started_at=1000.0,
            data={},
        )

        _run(rust_binary, str(db), "delete_task", task_id="task_c")

        store = TaskStore(db)
        try:
            assert store.get("task_c") is None
            assert store.find_run("run_c1") is None
        finally:
            store.close()
            del store
            gc.collect()


def test_rust_writes_disabled_task(rust_binary):
    from core.automation.store import TaskStore

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        _run(
            rust_binary,
            str(db),
            "save_task",
            task_id="task_d",
            enabled=False,
            data=_make_task_dict("task_d", title="Disabled", enabled=False),
        )

        store = TaskStore(db)
        try:
            task = store.get("task_d")
            assert task is not None
            assert task.enabled is False
        finally:
            store.close()
            del store
            gc.collect()


def test_rust_writes_workspace_column(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "tasks.db"
        _run(
            rust_binary,
            str(db),
            "add_run",
            run_id="run_e1",
            task_id="task_e",
            started_at=1000.0,
            data={"run_id": "run_e1"},
            workspace="ws_e",
        )

        import sqlite3

        conn = sqlite3.connect(str(db))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT workspace FROM task_runs WHERE run_id=?", ("run_e1",)
            ).fetchone()
            assert row["workspace"] == "ws_e"
        finally:
            conn.close()
            del conn
            gc.collect()
