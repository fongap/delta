//! Read-only shadow access to the Python Runtime's task store.
//!
//! The Python `core/automation/store.py` `TaskStore` writes scheduled
//! tasks and run history to a SQLite DB with two tables:
//! `scheduled_tasks` and `task_runs`. This module opens the same DB
//! read-only and exposes the stored data as parsed structs.

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

/// Read-only handle to a `tasks.db` file.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_db_has_no_tasks() {
        let reader = TaskStoreReader::open_in_memory().unwrap();
        assert!(reader.tasks().unwrap().is_empty());
        assert!(reader.runs("any").unwrap().is_empty());
        assert!(reader.find_run("any").unwrap().is_none());
    }
}
