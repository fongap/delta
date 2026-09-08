"""Old automation.db migration test (ADR-024 P0-1).

Verifies that:
1. A legacy DB without workspace column can be opened
2. workspace column is automatically added
3. Legacy rows are readable with default workspace ""
4. idx_runs_workspace index exists
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from core.automation.store import TaskStore


def test_taskstore_legacy_db_migrates_workspace():
    """Legacy automation.db without workspace column migrates correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "automation.db"
        # Step 1-2: Create legacy DB without workspace column
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE scheduled_tasks (
                id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                next_run REAL,
                data TEXT NOT NULL
            );
            CREATE TABLE task_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                data TEXT NOT NULL
            );
        """)
        # Step 3: Insert legacy row
        conn.execute(
            "INSERT INTO task_runs (run_id, task_id, started_at, data) VALUES (?, ?, ?, ?)",
            ("legacy_run", "task_1", 1000.0, "{}"),
        )
        conn.commit()
        conn.close()

        # Step 4-5: Open with TaskStore (triggers Rust migration)
        store = TaskStore(db)

        # Step 6-7: Legacy row is readable via Rust facade
        run = store.find_run("legacy_run")
        assert run is not None
        assert run.run_id == "legacy_run"
        assert run.task_id == "task_1"
        assert run.workspace == ""  # default empty

        # Step 8: Verify workspace column exists
        store.close()
        conn = sqlite3.connect(str(db))
        cursor = conn.execute("PRAGMA table_info(task_runs)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "workspace" in columns

        # Step 9: Verify idx_runs_workspace exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_runs_workspace'"
        )
        assert cursor.fetchone() is not None
        conn.close()


def test_taskstore_fresh_db_has_workspace():
    """Fresh DB created by TaskStore has workspace column from the start."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "automation.db"
        store = TaskStore(db)
        # Add a run and verify workspace is stored
        from core.automation.models import TaskRun

        run = TaskRun(
            task_id="task_1",
            run_id="run_1",
            started_at=1000.0,
            workspace="ws_1",
        )
        store.add_run(run)
        found = store.find_run("run_1")
        assert found is not None
        assert found.workspace == "ws_1"
        store.close()


def test_taskstore_repeated_open_is_idempotent():
    """Opening an existing DB multiple times is safe."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "automation.db"
        # First open + write
        store1 = TaskStore(db)
        from core.automation.models import ScheduledTask, Schedule

        task = ScheduledTask(
            title="Test",
            instructions="Test instructions",
            schedule=Schedule(kind="once"),
            workspace="ws_1",
        )
        store1.save(task)
        store1.close()
        # Second open — should not fail
        store2 = TaskStore(db)
        tasks = store2.list()
        assert len(tasks) == 1
        assert tasks[0].title == "Test"
        store2.close()
