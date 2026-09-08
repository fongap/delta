//! Task identity persistence authority — Rust implementation.
//!
//! This module provides the authoritative read-write interface to the
//! `automation.db` SQLite database with `scheduled_tasks` and `task_runs` tables.
//! All task identity operations (save, get, list, delete, due, add_run, find_run,
//! runs, task_for_run_session) are executed here. Python retains only a thin
//! facade over this Rust implementation.
//!
//! This is the R1 hard-cut: no Python SQLite writer exists. A missing,
//! incompatible, or failed Rust Core process raises an error.

use std::path::Path;

use rusqlite::{params, Connection};
use serde::Deserialize;
use serde_json::Value;

use crate::ShadowReadError;

/// One row from the `scheduled_tasks` table. The `data` column holds
/// the full `ScheduledTask.to_dict()` JSON blob.
#[derive(Debug, Clone)]
pub struct ScheduledTaskEntry {
    pub id: String,
    pub enabled: bool,
    pub next_run: Option<f64>,
    pub data: Value,
}

/// One row from the `task_runs` table. The `data` column holds the
/// full `TaskRun.to_dict()` JSON blob.
#[derive(Debug, Clone)]
pub struct TaskRunEntry {
    pub run_id: String,
    pub task_id: String,
    pub started_at: f64,
    pub data: Value,
    pub workspace: String,
}

/// Read-only handle to a `tasks.db` file (for shadow-read diagnostics).
pub struct TaskStoreReader {
    conn: Connection,
}

impl TaskStoreReader {
    /// Open the SQLite DB at `path` in read-only mode.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA query_only = ON;")?;
        Ok(Self { conn })
    }

    /// Open an in-memory DB (for tests).
    pub fn open_in_memory() -> Result<Self, ShadowReadError> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(
            r#"CREATE TABLE scheduled_tasks (
                id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                next_run REAL,
                data TEXT NOT NULL
            )"#,
        )?;
        conn.execute_batch(
            r#"CREATE TABLE task_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                data TEXT NOT NULL,
                workspace TEXT
            )"#,
        )?;
        Ok(Self { conn })
    }

    /// List all scheduled tasks.
    pub fn tasks(&self) -> Result<Vec<ScheduledTaskEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, enabled, next_run, data FROM scheduled_tasks
             ORDER BY next_run IS NULL, next_run",
        )?;
        let rows = stmt.query_map([], |row| {
            let data_str: String = row.get(3)?;
            let data: Value = serde_json::from_str(&data_str).unwrap_or(Value::Null);
            Ok(ScheduledTaskEntry {
                id: row.get(0)?,
                enabled: row.get::<_, i64>(1)? != 0,
                next_run: row.get(2)?,
                data,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// List all task runs for one task_id, ordered newest first.
    pub fn runs(&self, task_id: &str) -> Result<Vec<TaskRunEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, task_id, started_at, data, workspace FROM task_runs
             WHERE task_id = ? ORDER BY started_at DESC",
        )?;
        let rows = stmt.query_map(params![task_id], |row| {
            let data_str: String = row.get(3)?;
            let data: Value = serde_json::from_str(&data_str).unwrap_or(Value::Null);
            let workspace: Option<String> = row.get(4)?;
            Ok(TaskRunEntry {
                run_id: row.get(0)?,
                task_id: row.get(1)?,
                started_at: row.get(2)?,
                data,
                workspace: workspace.unwrap_or_default(),
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Find one task_run by run_id.
    pub fn find_run(&self, run_id: &str) -> Result<Option<TaskRunEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, task_id, started_at, data, workspace FROM task_runs
             WHERE run_id = ?",
        )?;
        let mut rows = stmt.query(params![run_id])?;
        if let Some(row) = rows.next()? {
            let data_str: String = row.get(3)?;
            let data: Value = serde_json::from_str(&data_str).unwrap_or(Value::Null);
            let workspace: Option<String> = row.get(4)?;
            Ok(Some(TaskRunEntry {
                run_id: row.get(0)?,
                task_id: row.get(1)?,
                started_at: row.get(2)?,
                data,
                workspace: workspace.unwrap_or_default(),
            }))
        } else {
            Ok(None)
        }
    }
}

/// Helper struct (deserialized from `data` column when callers want a
/// structured view). Mirrors a subset of the Python `ScheduledTask`
/// fields. Not exhaustive — the `data` blob has the full set.
#[derive(Debug, Deserialize)]
pub struct ScheduledTaskSummary {
    pub id: String,
    pub title: String,
    pub workspace: String,
    pub agent: String,
    pub enabled: bool,
}

/// The scheduled_task row structure for the Rust authoritative store.
/// The `data` column holds the full `ScheduledTask.to_dict()` JSON blob as a string.
#[derive(Debug, Clone)]
pub struct TaskEntry {
    pub id: String,
    pub enabled: bool,
    pub next_run: Option<f64>,
    pub data: String,
}

/// Read-write handle to a `tasks.db` file — the single authoritative TaskStore.
///
/// Mirrors the Python `core/automation/store.py` `TaskStore` API.
/// All methods delegate to SQLite directly. The caller passes pre-serialized
/// JSON blobs (`to_dict()` output on the Python side). The store does not
/// interpret the blob structure — it stores and retrieves opaque JSON.
pub struct TaskStore {
    conn: Connection,
}

/// Initialize the `scheduled_tasks` + `task_runs` tables.
fn init_schema(conn: &Connection) -> Result<(), rusqlite::Error> {
    conn.execute_batch(
        r#"CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            next_run REAL,
            data TEXT NOT NULL
        )"#,
    )?;
    conn.execute_batch(
        r#"CREATE TABLE IF NOT EXISTS task_runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            started_at REAL NOT NULL,
            data TEXT NOT NULL,
            workspace TEXT
        )"#,
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, started_at DESC)",
    )?;
    // ADR-007 workspace denormalization index
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_runs_workspace ON task_runs(workspace, started_at DESC)",
    )?;
    Ok(())
}

impl TaskStore {
    /// Open (or create) a `tasks.db` for read-write access.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        let conn = Connection::open(path)?;
        init_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open an in-memory DB (for tests).
    pub fn open_in_memory() -> Result<Self, ShadowReadError> {
        let conn = Connection::open_in_memory()?;
        init_schema(&conn)?;
        Ok(Self { conn })
    }

    // -- tasks ------------------------------------------------------------------

    /// Insert or replace a scheduled task.
    /// Mirrors Python `TaskStore.save`.
    pub fn save_task(
        &self,
        id: &str,
        enabled: bool,
        next_run: Option<f64>,
        data: &str,
    ) -> Result<(), ShadowReadError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO scheduled_tasks (id, enabled, next_run, data) \
             VALUES (?, ?, ?, ?)",
            params![id, enabled as i64, next_run, data],
        )?;
        Ok(())
    }

    /// Get a single task by id.
    /// Mirrors Python `TaskStore.get`.
    pub fn get_task(&self, task_id: &str) -> Result<Option<TaskEntry>, ShadowReadError> {
        let mut stmt = self
            .conn
            .prepare("SELECT id, enabled, next_run, data FROM scheduled_tasks WHERE id = ?")?;
        let mut rows = stmt.query(params![task_id])?;
        if let Some(row) = rows.next()? {
            Ok(Some(TaskEntry {
                id: row.get(0)?,
                enabled: row.get::<_, i64>(1)? != 0,
                next_run: row.get(2)?,
                data: row.get(3)?,
            }))
        } else {
            Ok(None)
        }
    }

    /// List all scheduled tasks.
    /// Mirrors Python `TaskStore.list`.
    pub fn list_tasks(&self) -> Result<Vec<TaskEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, enabled, next_run, data FROM scheduled_tasks
             ORDER BY next_run IS NULL, next_run",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(TaskEntry {
                id: row.get(0)?,
                enabled: row.get::<_, i64>(1)? != 0,
                next_run: row.get(2)?,
                data: row.get(3)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Delete a scheduled task and its runs.
    /// Mirrors Python `TaskStore.delete`.
    pub fn delete_task(&self, task_id: &str) -> Result<bool, ShadowReadError> {
        let rows = self
            .conn
            .execute("DELETE FROM scheduled_tasks WHERE id=?", params![task_id])?;
        self.conn
            .execute("DELETE FROM task_runs WHERE task_id=?", params![task_id])?;
        Ok(rows > 0)
    }

    /// List tasks that are due to run.
    /// Mirrors Python `TaskStore.due`.
    pub fn due_tasks(&self, now: f64) -> Result<Vec<TaskEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, enabled, next_run, data FROM scheduled_tasks
             WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?
             ORDER BY next_run",
        )?;
        let rows = stmt.query_map(params![now], |row| {
            Ok(TaskEntry {
                id: row.get(0)?,
                enabled: row.get::<_, i64>(1)? != 0,
                next_run: row.get(2)?,
                data: row.get(3)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    // -- runs -------------------------------------------------------------------

    /// Insert or replace a task run.
    /// Mirrors Python `TaskStore.add_run`.
    pub fn add_run(
        &self,
        run_id: &str,
        task_id: &str,
        started_at: f64,
        data: &str,
        workspace: &str,
    ) -> Result<(), ShadowReadError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO task_runs \
             (run_id, task_id, started_at, data, workspace) \
             VALUES (?, ?, ?, ?, ?)",
            params![run_id, task_id, started_at, data, workspace],
        )?;
        Ok(())
    }

    /// Find one task_run by run_id.
    /// Mirrors Python `TaskStore.find_run`.
    pub fn find_run(&self, run_id: &str) -> Result<Option<TaskRunEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, task_id, started_at, data, workspace FROM task_runs
             WHERE run_id = ?",
        )?;
        let mut rows = stmt.query(params![run_id])?;
        if let Some(row) = rows.next()? {
            let data_str: String = row.get(3)?;
            let data: Value = serde_json::from_str(&data_str).unwrap_or(Value::Null);
            let workspace: Option<String> = row.get(4)?;
            Ok(Some(TaskRunEntry {
                run_id: row.get(0)?,
                task_id: row.get(1)?,
                started_at: row.get(2)?,
                data,
                workspace: workspace.unwrap_or_default(),
            }))
        } else {
            Ok(None)
        }
    }

    /// Find the owning task of a run session ('__run__<run_id>').
    /// Mirrors Python `TaskStore.task_for_run_session`.
    pub fn task_for_run_session(
        &self,
        session_id: &str,
    ) -> Result<Option<TaskEntry>, ShadowReadError> {
        if !session_id.starts_with("__run__") {
            return Ok(None);
        }
        let run_id = &session_id["__run__".len()..];
        let run = self.find_run(run_id)?;
        if let Some(run) = run {
            self.get_task(&run.task_id)
        } else {
            Ok(None)
        }
    }

    /// List all task runs for one task_id, ordered newest first.
    /// Mirrors Python `TaskStore.runs`.
    pub fn runs(&self, task_id: &str, limit: usize) -> Result<Vec<TaskRunEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, task_id, started_at, data, workspace FROM task_runs
             WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
        )?;
        let rows = stmt.query_map(params![task_id, limit as i64], |row| {
            let data_str: String = row.get(3)?;
            let data: Value = serde_json::from_str(&data_str).unwrap_or(Value::Null);
            let workspace: Option<String> = row.get(4)?;
            Ok(TaskRunEntry {
                run_id: row.get(0)?,
                task_id: row.get(1)?,
                started_at: row.get(2)?,
                data,
                workspace: workspace.unwrap_or_default(),
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Close the database connection.
    pub fn close(&self) {
        // conn.close() takes ownership; we can't call it from &self.
        // The connection will be closed when TaskStore is dropped.
        // The delta_core cache manages the lifecycle.
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_db_has_no_tasks() {
        let store = TaskStore::open_in_memory().unwrap();
        assert!(store.list_tasks().unwrap().is_empty());
        assert!(store.runs("any", 50).unwrap().is_empty());
        assert!(store.find_run("any").unwrap().is_none());
    }

    #[test]
    fn writer_save_and_read_task() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();
        store
            .save_task(
                "task_1",
                true,
                Some(1000.0),
                r#"{"id":"task_1","title":"Test"}"#,
            )
            .unwrap();

        let task = store.get_task("task_1").unwrap();
        assert!(task.is_some());
        let task = task.unwrap();
        assert_eq!(task.id, "task_1");
        assert!(task.enabled);
    }

    #[test]
    fn writer_add_and_find_run() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();
        store
            .add_run(
                "run_1",
                "task_1",
                1000.0,
                r#"{"run_id":"run_1","status":"completed"}"#,
                "ws_1",
            )
            .unwrap();

        let run = store.find_run("run_1").unwrap();
        assert!(run.is_some());
        let run = run.unwrap();
        assert_eq!(run.task_id, "task_1");
        assert_eq!(run.workspace, "ws_1");
    }

    #[test]
    fn writer_delete_task_cascades_runs() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();
        store
            .save_task("task_1", true, None, r#"{"id":"task_1"}"#)
            .unwrap();
        store
            .add_run("run_1", "task_1", 1000.0, r#"{}"#, "")
            .unwrap();
        store
            .add_run("run_2", "task_1", 1001.0, r#"{}"#, "")
            .unwrap();

        let deleted = store.delete_task("task_1").unwrap();
        assert!(deleted);

        assert!(store.list_tasks().unwrap().is_empty());
        assert!(store.runs("task_1", 50).unwrap().is_empty());
    }

    #[test]
    fn writer_delete_nonexistent_returns_false() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();
        let deleted = store.delete_task("nope").unwrap();
        assert!(!deleted);
    }

    #[test]
    fn list_tasks_ordering() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();

        // Task with next_run
        store
            .save_task("task_a", true, Some(1000.0), r#"{"id":"task_a"}"#)
            .unwrap();
        // Task without next_run (should come after)
        store
            .save_task("task_b", true, None, r#"{"id":"task_b"}"#)
            .unwrap();
        // Task with earlier next_run
        store
            .save_task("task_c", true, Some(500.0), r#"{"id":"task_c"}"#)
            .unwrap();

        let tasks = store.list_tasks().unwrap();
        assert_eq!(tasks.len(), 3);
        // Order: task_c (500), task_a (1000), task_b (NULL)
        assert_eq!(tasks[0].id, "task_c");
        assert_eq!(tasks[1].id, "task_a");
        assert_eq!(tasks[2].id, "task_b");
    }

    #[test]
    fn due_tasks_filters_correctly() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();

        // Enabled, due now
        store
            .save_task("task_due", true, Some(1000.0), r#"{"id":"task_due"}"#)
            .unwrap();
        // Enabled, not due yet
        store
            .save_task("task_future", true, Some(2000.0), r#"{"id":"task_future"}"#)
            .unwrap();
        // Disabled
        store
            .save_task(
                "task_disabled",
                false,
                Some(500.0),
                r#"{"id":"task_disabled"}"#,
            )
            .unwrap();
        // No next_run
        store
            .save_task("task_none", true, None, r#"{"id":"task_none"}"#)
            .unwrap();

        let due = store.due_tasks(1500.0).unwrap();
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].id, "task_due");
    }

    #[test]
    fn runs_limit() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();

        store
            .save_task("task_1", true, None, r#"{"id":"task_1"}"#)
            .unwrap();
        for i in 1..=10 {
            store
                .add_run(&format!("run_{}", i), "task_1", i as f64, "{}", "")
                .unwrap();
        }

        let runs = store.runs("task_1", 3).unwrap();
        assert_eq!(runs.len(), 3);
        // Should be ordered by started_at DESC (newest first)
        assert_eq!(runs[0].run_id, "run_10");
        assert_eq!(runs[1].run_id, "run_9");
        assert_eq!(runs[2].run_id, "run_8");
    }

    #[test]
    fn task_for_run_session() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();

        store
            .save_task("task_1", true, None, r#"{"id":"task_1"}"#)
            .unwrap();
        store.add_run("run_1", "task_1", 1000.0, "{}", "").unwrap();

        let task = store.task_for_run_session("__run__run_1").unwrap();
        assert!(task.is_some());
        assert_eq!(task.unwrap().id, "task_1");

        let task = store.task_for_run_session("__run__nonexistent").unwrap();
        assert!(task.is_none());

        let task = store.task_for_run_session("not_a_run").unwrap();
        assert!(task.is_none());
    }

    #[test]
    fn close_is_idempotent() {
        let store = TaskStore::open_in_memory().unwrap();
        store.close();
        store.close(); // Should not panic
    }
}
