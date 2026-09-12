//! Unified read-write access to the task store (`automation.db`).
//!
//! The Python `core/automation/store.py` `TaskStore` previously wrote
//! scheduled tasks and run history to a SQLite DB with two tables:
//! `scheduled_tasks` and `task_runs`. After the R1 Task Identity
//! Hard-Cut (ADR-024), Rust is the sole authority for this domain.
//! This module opens the DB read-write and exposes both reads and
//! writes as parsed structs.

use std::path::Path;

use rusqlite::{params, Connection};
use serde::Deserialize;
use serde_json::Value;

use crate::ShadowReadError;

/// Extract run status from a run's JSON data.
fn run_data_json_get_status(data: &str) -> String {
    let value: Value = match serde_json::from_str(data) {
        Ok(v) => v,
        Err(_) => return "error".to_string(),
    };
    value
        .get("status")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "error".to_string())
}

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

/// Unified read-write handle to a `tasks.db` file.
///
/// After the R1 Task Identity Hard-Cut, this is the sole authority
/// for the `task_identity` domain. Mirrors the Python
/// `core/automation/store.py` `TaskStore` read+write path.
pub struct TaskStore {
    conn: Connection,
}

/// Initialize the `scheduled_tasks` + `task_runs` tables with
/// idempotent workspace migration.
///
/// Handles three cases:
/// 1. Fresh DB — CREATE TABLE includes workspace column
/// 2. Legacy DB without workspace — ALTER TABLE adds it
/// 3. Current DB with workspace — no-op
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
    // Idempotent workspace migration: check if column exists before ALTER.
    let has_workspace: bool = {
        let mut stmt = conn.prepare("PRAGMA table_info(task_runs)")?;
        let rows: Vec<String> = stmt
            .query_map([], |row| row.get(1))?
            .filter_map(|r| r.ok())
            .collect();
        rows.iter().any(|name| name == "workspace")
    };
    if !has_workspace {
        conn.execute_batch("ALTER TABLE task_runs ADD COLUMN workspace TEXT")?;
    }
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
    pub fn get_task(&self, task_id: &str) -> Result<Option<ScheduledTaskEntry>, ShadowReadError> {
        let mut stmt = self
            .conn
            .prepare("SELECT id, enabled, next_run, data FROM scheduled_tasks WHERE id = ?")?;
        let mut rows = stmt.query(params![task_id])?;
        if let Some(row) = rows.next()? {
            let data_str: String = row.get(3)?;
            let data: Value = serde_json::from_str(&data_str).unwrap_or(Value::Null);
            Ok(Some(ScheduledTaskEntry {
                id: row.get(0)?,
                enabled: row.get::<_, i64>(1)? != 0,
                next_run: row.get(2)?,
                data,
            }))
        } else {
            Ok(None)
        }
    }

    /// List all scheduled tasks.
    pub fn list_tasks(&self) -> Result<Vec<ScheduledTaskEntry>, ShadowReadError> {
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

    /// List due tasks (enabled, next_run <= now).
    pub fn due_tasks(&self, now: f64) -> Result<Vec<ScheduledTaskEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, enabled, next_run, data FROM scheduled_tasks
             WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?
             ORDER BY next_run",
        )?;
        let rows = stmt.query_map(params![now], |row| {
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

    /// Delete a scheduled task and its runs.
    pub fn delete_task(&self, task_id: &str) -> Result<bool, ShadowReadError> {
        let rows = self
            .conn
            .execute("DELETE FROM scheduled_tasks WHERE id=?", params![task_id])?;
        self.conn
            .execute("DELETE FROM task_runs WHERE task_id=?", params![task_id])?;
        Ok(rows > 0)
    }

    // -- runs -------------------------------------------------------------------

    /// Insert or replace a task run.
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

    /// The owning task of a run session ('__run__<run_id>'), or None.
    pub fn task_for_run_session(
        &self,
        session_id: &str,
    ) -> Result<Option<ScheduledTaskEntry>, ShadowReadError> {
        if !session_id.starts_with("__run__") {
            return Ok(None);
        }
        let run_id = &session_id["__run__".len()..];
        match self.find_run(run_id)? {
            Some(run) => self.get_task(&run.task_id),
            None => Ok(None),
        }
    }

    /// List task runs for one task_id, ordered newest first.
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

    /// Atomically complete a run and update task stats.
    ///
    /// Performs in a single transaction:
    /// 1. Insert/replace the run in `task_runs`
    /// 2. Load task data, increment run_count, set last_run/last_status
    /// 3. Check max_runs exhaustion: if run_count >= max_runs, set enabled=false, next_run=NULL
    /// 4. Apply Python-supplied next_run (when not exhausted) to both JSON and SQL columns
    /// 5. Update task in `scheduled_tasks` — JSON blob AND indexed columns together
    ///
    /// AF-02 fix: the SQL indexed columns (`enabled`, `next_run`) are updated
    /// in the same transaction as the JSON blob, so `due_tasks()` queries
    /// always see the same state as the JSON.
    ///
    /// `next_run` is computed by Python's `compute_next_run()` (ADR-044: pure
    /// function stays in Python) and passed in. Rust is the sole persistor.
    ///
    /// Returns the updated task data JSON (for Python to update its cache).
    pub fn complete_run(
        &mut self,
        run_id: &str,
        task_id: &str,
        started_at: f64,
        run_data: &str,
        workspace: &str,
        finished_at: f64,
        next_run: Option<f64>,
    ) -> Result<Value, ShadowReadError> {
        let tx = self.conn.transaction()?;

        // 1. Insert/replace the run
        tx.execute(
            "INSERT OR REPLACE INTO task_runs (run_id, task_id, started_at, data, workspace) VALUES (?, ?, ?, ?, ?)",
            params![run_id, task_id, started_at, run_data, workspace],
        )?;

        // 2. Load task data
        let task_data: Option<String> = {
            let mut stmt = tx.prepare("SELECT data FROM scheduled_tasks WHERE id = ?")?;
            stmt.query_row(params![task_id], |row| row.get(0)).ok()
        };

        let mut task_json: Value = if let Some(d) = task_data {
            serde_json::from_str(&d).unwrap_or(Value::Null)
        } else {
            Value::Null
        };

        if task_json.is_null() {
            return Err(ShadowReadError::Parse(format!(
                "task {} not found",
                task_id
            )));
        }

        // 3. Update task stats
        let run_count = task_json
            .get("run_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0)
            + 1;
        let max_runs = task_json
            .get("max_runs")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        let last_status = run_data_json_get_status(run_data);

        task_json["run_count"] = Value::from(run_count as i64);
        task_json["last_run"] = Value::from(finished_at);
        task_json["last_status"] = Value::String(last_status.to_string());

        // 4. Check max_runs exhaustion — Rust is the single authority for this.
        //    If exhausted, override next_run to None and enabled to false.
        let exhausted = max_runs > 0 && run_count >= max_runs;
        let final_enabled: bool;
        let final_next_run: Option<f64>;
        if exhausted {
            task_json["enabled"] = Value::Bool(false);
            task_json["next_run"] = Value::Null;
            final_enabled = false;
            final_next_run = None;
        } else {
            task_json["enabled"] = Value::Bool(true);
            match next_run {
                Some(nr) => {
                    task_json["next_run"] = Value::from(nr);
                    final_next_run = Some(nr);
                }
                None => {
                    task_json["next_run"] = Value::Null;
                    final_next_run = None;
                }
            }
            final_enabled = true;
        }

        // 5. Update task — JSON blob AND SQL indexed columns in ONE statement.
        let updated_data = task_json.to_string();
        tx.execute(
            "UPDATE scheduled_tasks SET data = ?, enabled = ?, next_run = ? WHERE id = ?",
            params![&updated_data, final_enabled as i64, final_next_run, task_id],
        )?;

        tx.commit()?;
        Ok(task_json)
    }

    /// Close the connection. Idempotent.
    pub fn close(&self) {
        // Connection is dropped when TaskStore is dropped.
        // This method exists for API compatibility with the Python facade.
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
        store
            .save_task("task_1", true, Some(2000.0), r#"{"id":"task_1"}"#)
            .unwrap();
        store
            .save_task("task_2", true, Some(1000.0), r#"{"id":"task_2"}"#)
            .unwrap();
        store
            .save_task("task_3", true, None, r#"{"id":"task_3"}"#)
            .unwrap();

        let tasks = store.list_tasks().unwrap();
        assert_eq!(tasks.len(), 3);
        // next_run=None goes last, then sorted by next_run
        assert_eq!(tasks[0].id, "task_2");
        assert_eq!(tasks[1].id, "task_1");
        assert_eq!(tasks[2].id, "task_3");
    }

    #[test]
    fn due_tasks_filters_correctly() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        let store = TaskStore::open(&db).unwrap();
        // Due now
        store
            .save_task("task_due", true, Some(100.0), r#"{"id":"task_due"}"#)
            .unwrap();
        // Not due yet
        store
            .save_task(
                "task_future",
                true,
                Some(999999.0),
                r#"{"id":"task_future"}"#,
            )
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
            .save_task("task_no_next", true, None, r#"{"id":"task_no_next"}"#)
            .unwrap();

        let due = store.due_tasks(200.0).unwrap();
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
        // DESC order: newest first
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

        // Non-run session
        assert!(store.task_for_run_session("other").unwrap().is_none());
        // Non-existent run
        assert!(store.task_for_run_session("__run__nope").unwrap().is_none());
    }

    #[test]
    fn close_is_idempotent() {
        let store = TaskStore::open_in_memory().unwrap();
        store.close(); // Should not panic
    }

    #[test]
    fn complete_run_syncs_sql_indexed_columns() {
        // AF-02: complete_run must update SQL enabled/next_run in the same
        // transaction as the JSON blob so due_tasks() never reads stale columns.
        let mut store = TaskStore::open_in_memory().unwrap();
        let task_json = r#"{"id":"task_1","title":"t","run_count":0,"max_runs":1}"#;
        store
            .save_task("task_1", true, Some(1000.0), task_json)
            .unwrap();

        store
            .complete_run(
                "run_1",
                "task_1",
                2000.0,
                r#"{"run_id":"run_1","status":"ok"}"#,
                "ws",
                2100.0,
                None, // Python would pass None because max_runs=1 is exhausted
            )
            .unwrap();

        // SQL enabled column must be 0 (disabled) — NOT left stale at 1.
        let task = store.get_task("task_1").unwrap().unwrap();
        assert!(!task.enabled, "exhausted task must have SQL enabled=0");
        assert!(
            task.next_run.is_none(),
            "exhausted task must have SQL next_run=NULL"
        );

        // due_tasks() reads SQL columns; an exhausted task must not be due.
        let due = store.due_tasks(f64::MAX).unwrap();
        assert!(due.is_empty(), "exhausted task must not be due");

        // JSON blob must also be disabled (consistent with SQL).
        let blob = &task.data;
        assert_eq!(blob["enabled"], false);
        assert_eq!(blob["run_count"], 1);
    }

    #[test]
    fn complete_run_non_exhausted_uses_python_next_run() {
        // AF-02: when not exhausted, the Python-computed next_run is persisted
        // to BOTH JSON and SQL columns.
        let mut store = TaskStore::open_in_memory().unwrap();
        let task_json = r#"{"id":"task_1","title":"t","run_count":0,"max_runs":0}"#;
        store
            .save_task("task_1", true, Some(1000.0), task_json)
            .unwrap();

        store
            .complete_run(
                "run_1",
                "task_1",
                2000.0,
                r#"{"run_id":"run_1","status":"ok"}"#,
                "ws",
                2100.0,
                Some(5000.0), // Python computed next_run
            )
            .unwrap();

        let task = store.get_task("task_1").unwrap().unwrap();
        assert!(task.enabled);
        assert_eq!(
            task.next_run,
            Some(5000.0),
            "SQL next_run must match Python value"
        );
        assert_eq!(
            task.data["next_run"], 5000.0_f64,
            "JSON next_run must match SQL"
        );
        assert_eq!(task.data["run_count"], 1);
    }

    #[test]
    fn legacy_db_without_workspace_migrates() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        // Create legacy DB without workspace column
        {
            let conn = Connection::open(&db).unwrap();
            conn.execute_batch(
                r#"CREATE TABLE scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run REAL,
                    data TEXT NOT NULL
                )"#,
            )
            .unwrap();
            conn.execute_batch(
                r#"CREATE TABLE task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    data TEXT NOT NULL
                )"#,
            )
            .unwrap();
            // Insert a legacy row without workspace
            conn.execute(
                "INSERT INTO task_runs (run_id, task_id, started_at, data) VALUES (?, ?, ?, ?)",
                params!["legacy_run", "task_1", 1000.0, "{}"],
            )
            .unwrap();
            conn.close().ok();
        }
        // Open with TaskStore — should migrate
        let store = TaskStore::open(&db).unwrap();
        // Legacy row should be readable
        let run = store.find_run("legacy_run").unwrap();
        assert!(run.is_some());
        let run = run.unwrap();
        assert_eq!(run.workspace, ""); // default empty
                                       // Verify workspace column exists via direct connection
        drop(store);
        let conn = Connection::open(&db).unwrap();
        let has_workspace: bool = {
            let mut stmt = conn.prepare("PRAGMA table_info(task_runs)").unwrap();
            let rows: Vec<String> = stmt
                .query_map([], |row| row.get(1))
                .unwrap()
                .filter_map(|r| r.ok())
                .collect();
            rows.iter().any(|name| name == "workspace")
        };
        assert!(has_workspace);
        // Verify index exists
        let has_index: bool = {
            let mut stmt = conn
                .prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_runs_workspace'")
                .unwrap();
            let mut rows = stmt.query([]).unwrap();
            rows.next().unwrap().is_some()
        };
        assert!(has_index);
    }

    #[test]
    fn repeated_open_is_idempotent() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("tasks.db");
        // Open, write, close
        {
            let store = TaskStore::open(&db).unwrap();
            store
                .save_task("task_1", true, None, r#"{"id":"task_1"}"#)
                .unwrap();
        }
        // Open again — should not fail
        let store = TaskStore::open(&db).unwrap();
        let tasks = store.list_tasks().unwrap();
        assert_eq!(tasks.len(), 1);
        assert_eq!(tasks[0].id, "task_1");
    }
}
