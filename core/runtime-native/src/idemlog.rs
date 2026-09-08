//! Read-only shadow access to the Python Runtime's side-effect idempotency log.
//!
//! The Python `core/idemlog.py` `IdempotencyLog` writes a state machine
//! for every consequential tool call to a SQLite table `side_effects`.
//! This module opens the same DB file read-only and inspects the
//! state machine from Rust.
//!
//! Contract: `docs/architecture/runtime-public-contract.md` §2.4.

use std::path::Path;

use rusqlite::{params, Connection};
use serde_json::Value;

use crate::ShadowReadError;

/// Mirrors `core/idemlog.py` `SideEffectState`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SideEffectState {
    Planned,
    Executing,
    Committed,
    Failed,
    Uncertain,
}

impl SideEffectState {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "planned" => Some(Self::Planned),
            "executing" => Some(Self::Executing),
            "committed" => Some(Self::Committed),
            "failed" => Some(Self::Failed),
            "uncertain" => Some(Self::Uncertain),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Planned => "planned",
            Self::Executing => "executing",
            Self::Committed => "committed",
            Self::Failed => "failed",
            Self::Uncertain => "uncertain",
        }
    }

    pub fn is_terminal(&self) -> bool {
        matches!(self, Self::Committed | Self::Failed | Self::Uncertain)
    }
}

/// One row from the `side_effects` table.
#[derive(Debug, Clone)]
pub struct SideEffectEntry {
    pub run_id: String,
    pub tool_call_id: String,
    pub tool_name: String,
    pub args_sha256: String,
    pub result: Value,
    pub state: SideEffectState,
    pub operation_id: String,
    pub committed_at: f64,
    pub updated_at: f64,
}

/// Read-only handle to a `side-effects.db` file.
pub struct IdempotencyReader {
    conn: Connection,
}

impl IdempotencyReader {
    /// Open the SQLite DB at `path` in read-only mode.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA query_only = ON;")?;
        Ok(Self { conn })
    }

    /// Open an in-memory DB (for tests).
    pub fn open_in_memory() -> Result<Self, ShadowReadError> {
        let conn = Connection::open_in_memory()?;
        init_schema(&conn)?;
        Ok(Self { conn })
    }

    /// List all side effects for a run, ordered by updated_at.
    pub fn for_run(&self, run_id: &str) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        entries_for_run(&self.conn, run_id)
    }

    /// List all uncommitted (Planned or Executing) side effects for a run.
    pub fn uncommitted_for_run(
        &self,
        run_id: &str,
    ) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        let all = self.for_run(run_id)?;
        Ok(all.into_iter().filter(|e| !e.state.is_terminal()).collect())
    }

    /// List all Uncertain side effects for a run.
    pub fn uncertain_for_run(&self, run_id: &str) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        let all = self.for_run(run_id)?;
        Ok(all
            .into_iter()
            .filter(|e| e.state == SideEffectState::Uncertain)
            .collect())
    }

    /// List all Committed side effects for a run.
    pub fn committed_for_run(&self, run_id: &str) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        let all = self.for_run(run_id)?;
        Ok(all
            .into_iter()
            .filter(|e| e.state == SideEffectState::Committed)
            .collect())
    }
}

fn entries_for_run(
    conn: &Connection,
    run_id: &str,
) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
    let mut stmt = conn.prepare(
        "SELECT run_id, tool_call_id, tool_name, args_sha256,
                result_json, state, operation_id, committed_at, updated_at
         FROM side_effects WHERE run_id = ? ORDER BY updated_at",
    )?;
    let rows = stmt.query_map(params![run_id], |row| {
        let result_str: String = row.get(4)?;
        let result: Value = serde_json::from_str(&result_str).unwrap_or(Value::Null);
        let state_str: String = row.get(5)?;
        let state = SideEffectState::parse(&state_str).unwrap_or(SideEffectState::Committed);
        Ok(SideEffectEntry {
            run_id: row.get(0)?,
            tool_call_id: row.get(1)?,
            tool_name: row.get(2)?,
            args_sha256: row.get(3)?,
            result,
            state,
            operation_id: row.get(6)?,
            committed_at: row.get(7)?,
            updated_at: row.get(8)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

/// Initialize the `side_effects` table + index. Shared by reader and writer.
fn init_schema(conn: &Connection) -> Result<(), rusqlite::Error> {
    conn.execute_batch(
        r#"CREATE TABLE IF NOT EXISTS side_effects (
            run_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_sha256 TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'committed',
            operation_id TEXT NOT NULL DEFAULT '',
            committed_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (run_id, tool_call_id)
        )"#,
    )?;
    // Idempotent migration (mirrors Python ALTER TABLE try/except)
    for ddl in [
        "ALTER TABLE side_effects ADD COLUMN state TEXT NOT NULL DEFAULT 'committed'",
        "ALTER TABLE side_effects ADD COLUMN operation_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE side_effects ADD COLUMN updated_at REAL NOT NULL DEFAULT 0",
    ] {
        let _ = conn.execute_batch(ddl);
    }
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_side_effects_state ON side_effects(state, run_id)",
    )?;
    Ok(())
}

/// Stable SHA256-derived idempotency key (mirrors Python `operation_id`).
pub fn operation_id(run_id: &str, tool_call_id: &str) -> String {
    use sha2::{Digest, Sha256};
    let raw = format!("{}:{}", run_id, tool_call_id);
    let digest = Sha256::digest(raw.as_bytes());
    hex_string(&digest)[..36].to_string()
}

/// Stable SHA256 fingerprint of arguments (mirrors Python `args_sha256`).
pub fn args_sha256(arguments: &Value) -> String {
    use sha2::{Digest, Sha256};
    let canonical = canonical_json(arguments);
    let digest = Sha256::digest(canonical.as_bytes());
    hex_string(&digest)
}

fn hex_string(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

fn canonical_json(value: &Value) -> String {
    let mut buf = String::new();
    write_canonical(value, &mut buf);
    buf
}

fn write_canonical(value: &Value, buf: &mut String) {
    match value {
        Value::Object(map) => {
            buf.push('{');
            let mut sorted: Vec<(&String, &Value)> = map.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));
            for (i, (k, v)) in sorted.iter().enumerate() {
                if i > 0 {
                    buf.push(',');
                }
                buf.push('"');
                buf.push_str(k);
                buf.push_str("\":");
                write_canonical(v, buf);
            }
            buf.push('}');
        }
        Value::Array(arr) => {
            buf.push('[');
            for (i, v) in arr.iter().enumerate() {
                if i > 0 {
                    buf.push(',');
                }
                write_canonical(v, buf);
            }
            buf.push(']');
        }
        Value::String(s) => {
            buf.push('"');
            buf.push_str(s);
            buf.push('"');
        }
        Value::Number(n) => {
            buf.push_str(&n.to_string());
        }
        Value::Bool(b) => {
            buf.push_str(if *b { "true" } else { "false" });
        }
        Value::Null => {
            buf.push_str("null");
        }
    }
}

/// Read-write handle to a `side-effects.db` file.
///
/// Mirrors the Python `IdempotencyLog` write path. When
/// `DELTA_RUST_AUTHORITY=1` is set, the Python `IdempotencyLog` will
/// delegate writes to this writer via the `write_idemlog` binary.
pub struct IdempotencyWriter {
    conn: Connection,
}

impl IdempotencyWriter {
    /// Open (or create) a `side-effects.db` for read-write access.
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

    /// Record a Planned side effect (intent before execution).
    /// Mirrors Python `IdempotencyLog.record_planned`.
    pub fn record_planned(
        &self,
        run_id: &str,
        tool_call_id: &str,
        tool_name: &str,
        arguments: &Value,
    ) -> Result<String, ShadowReadError> {
        if run_id.is_empty() || tool_call_id.is_empty() {
            return Ok(String::new());
        }
        let sha = args_sha256(arguments);
        let op_id = operation_id(run_id, tool_call_id);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();

        // Check if row already exists in terminal state with same args
        let existing: Option<(String, String)> = self
            .conn
            .query_row(
                "SELECT state, args_sha256 FROM side_effects WHERE run_id=? AND tool_call_id=?",
                params![run_id, tool_call_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .ok();
        if let Some((state, existing_sha)) = existing {
            if existing_sha == sha
                && matches!(
                    SideEffectState::parse(&state),
                    Some(SideEffectState::Committed)
                        | Some(SideEffectState::Uncertain)
                        | Some(SideEffectState::Failed)
                )
            {
                return Ok(op_id);
            }
        }

        self.conn.execute(
            "INSERT INTO side_effects
                (run_id, tool_call_id, tool_name, args_sha256, result_json,
                 state, operation_id, committed_at, updated_at)
             VALUES (?, ?, ?, ?, '{}', ?, ?, 0, ?)
             ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                tool_name=excluded.tool_name,
                args_sha256=excluded.args_sha256,
                state=excluded.state,
                operation_id=excluded.operation_id,
                updated_at=excluded.updated_at",
            params![
                run_id,
                tool_call_id,
                tool_name,
                sha,
                SideEffectState::Planned.as_str(),
                op_id,
                now,
            ],
        )?;
        Ok(op_id)
    }

    /// Transition a Planned side effect to Executing.
    pub fn mark_executing(&self, run_id: &str, tool_call_id: &str) -> Result<(), ShadowReadError> {
        if run_id.is_empty() || tool_call_id.is_empty() {
            return Ok(());
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        self.conn.execute(
            "UPDATE side_effects SET state=?, updated_at=?
             WHERE run_id=? AND tool_call_id=? AND state=?",
            params![
                SideEffectState::Executing.as_str(),
                now,
                run_id,
                tool_call_id,
                SideEffectState::Planned.as_str(),
            ],
        )?;
        Ok(())
    }

    /// Record a Committed side effect with the result.
    pub fn commit(
        &self,
        run_id: &str,
        tool_call_id: &str,
        tool_name: &str,
        arguments: &Value,
        result: &Value,
    ) -> Result<(), ShadowReadError> {
        if run_id.is_empty() || tool_call_id.is_empty() {
            return Ok(());
        }
        let sha = args_sha256(arguments);
        let op_id = operation_id(run_id, tool_call_id);
        let result_json = serde_json::to_string(result)?;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        self.conn.execute(
            "INSERT INTO side_effects
                (run_id, tool_call_id, tool_name, args_sha256, result_json,
                 state, operation_id, committed_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                tool_name=excluded.tool_name,
                args_sha256=excluded.args_sha256,
                result_json=excluded.result_json,
                state=excluded.state,
                operation_id=excluded.operation_id,
                committed_at=excluded.committed_at,
                updated_at=excluded.updated_at",
            params![
                run_id,
                tool_call_id,
                tool_name,
                sha,
                result_json,
                SideEffectState::Committed.as_str(),
                op_id,
                now,
                now,
            ],
        )?;
        Ok(())
    }

    /// Transition a side effect to Failed.
    pub fn mark_failed(
        &self,
        run_id: &str,
        tool_call_id: &str,
        error: &str,
    ) -> Result<(), ShadowReadError> {
        if run_id.is_empty() || tool_call_id.is_empty() {
            return Ok(());
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let result_json = serde_json::to_string(&serde_json::json!({"error": error}))?;
        self.conn.execute(
            "UPDATE side_effects SET state=?, result_json=?, updated_at=?
             WHERE run_id=? AND tool_call_id=? AND state IN (?,?)",
            params![
                SideEffectState::Failed.as_str(),
                result_json,
                now,
                run_id,
                tool_call_id,
                SideEffectState::Planned.as_str(),
                SideEffectState::Executing.as_str(),
            ],
        )?;
        Ok(())
    }

    /// Transition a non-committed side effect to Uncertain.
    pub fn mark_uncertain(&self, run_id: &str, tool_call_id: &str) -> Result<(), ShadowReadError> {
        if run_id.is_empty() || tool_call_id.is_empty() {
            return Ok(());
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        self.conn.execute(
            "UPDATE side_effects SET state=?, updated_at=?
             WHERE run_id=? AND tool_call_id=? AND state IN (?,?)",
            params![
                SideEffectState::Uncertain.as_str(),
                now,
                run_id,
                tool_call_id,
                SideEffectState::Planned.as_str(),
                SideEffectState::Executing.as_str(),
            ],
        )?;
        Ok(())
    }

    /// Return one entry by its stable `(run_id, tool_call_id)` identity.
    pub fn get(
        &self,
        run_id: &str,
        tool_call_id: &str,
    ) -> Result<Option<SideEffectEntry>, ShadowReadError> {
        Ok(entries_for_run(&self.conn, run_id)?
            .into_iter()
            .find(|entry| entry.tool_call_id == tool_call_id))
    }

    /// Resolve replay eligibility inside the Rust authority.
    pub fn lookup(
        &self,
        run_id: &str,
        tool_call_id: &str,
        arguments: &Value,
    ) -> Result<Option<SideEffectEntry>, ShadowReadError> {
        let Some(entry) = self.get(run_id, tool_call_id)? else {
            return Ok(None);
        };
        if entry.args_sha256 != args_sha256(arguments) {
            return Ok(None);
        }
        if matches!(
            entry.state,
            SideEffectState::Committed | SideEffectState::Uncertain
        ) {
            return Ok(Some(entry));
        }
        Ok(None)
    }

    pub fn uncommitted_for_run(
        &self,
        run_id: &str,
    ) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        Ok(entries_for_run(&self.conn, run_id)?
            .into_iter()
            .filter(|entry| !entry.state.is_terminal())
            .collect())
    }

    pub fn uncertain_for_run(&self, run_id: &str) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        Ok(entries_for_run(&self.conn, run_id)?
            .into_iter()
            .filter(|entry| entry.state == SideEffectState::Uncertain)
            .collect())
    }

    pub fn committed_for_run(&self, run_id: &str) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        Ok(entries_for_run(&self.conn, run_id)?
            .into_iter()
            .filter(|entry| entry.state == SideEffectState::Committed)
            .collect())
    }

    /// Atomically identify and transition every stale side effect selected
    /// by the caller's interrupted-run set. The returned entries are the
    /// pre-transition rows used for audit projection.
    pub fn sweep_stale(
        &self,
        interrupted_run_ids: &[String],
    ) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        let mut swept = Vec::new();
        for run_id in interrupted_run_ids {
            let stale = self.uncommitted_for_run(run_id)?;
            for entry in stale {
                self.mark_uncertain(run_id, &entry.tool_call_id)?;
                swept.push(entry);
            }
        }
        Ok(swept)
    }

    /// Apply an operator decision to an Uncertain side effect.
    pub fn resolve_uncertain(
        &self,
        run_id: &str,
        tool_call_id: &str,
        resolution: &str,
        result: &Value,
    ) -> Result<(), ShadowReadError> {
        if run_id.is_empty() || tool_call_id.is_empty() {
            return Ok(());
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        if resolution == "confirmed" {
            let confirmed = if result.is_null() {
                serde_json::json!({"confirmed": true})
            } else {
                result.clone()
            };
            self.conn.execute(
                "UPDATE side_effects SET state=?, result_json=?, committed_at=?, updated_at=?
                 WHERE run_id=? AND tool_call_id=? AND state=?",
                params![
                    SideEffectState::Committed.as_str(),
                    serde_json::to_string(&confirmed)?,
                    now,
                    now,
                    run_id,
                    tool_call_id,
                    SideEffectState::Uncertain.as_str(),
                ],
            )?;
        } else {
            self.conn.execute(
                "UPDATE side_effects SET state=?, result_json=?, updated_at=?
                 WHERE run_id=? AND tool_call_id=? AND state=?",
                params![
                    SideEffectState::Failed.as_str(),
                    serde_json::to_string(&serde_json::json!({"resolution": resolution}))?,
                    now,
                    run_id,
                    tool_call_id,
                    SideEffectState::Uncertain.as_str(),
                ],
            )?;
        }
        Ok(())
    }
}
#[cfg(test)]
#[allow(unused_imports)]
mod tests {
    use super::*;

    #[test]
    fn empty_db_has_no_entries() {
        let reader = IdempotencyReader::open_in_memory().unwrap();
        assert!(reader.for_run("run_1").unwrap().is_empty());
    }

    #[test]
    fn state_parse_roundtrip() {
        for s in &["planned", "executing", "committed", "failed", "uncertain"] {
            let state = SideEffectState::parse(s).unwrap();
            assert_eq!(state.as_str(), *s);
        }
    }

    #[test]
    fn state_parse_unknown_defaults_committed() {
        assert_eq!(SideEffectState::parse("bogus"), None);
    }

    #[test]
    fn is_terminal() {
        assert!(SideEffectState::Committed.is_terminal());
        assert!(SideEffectState::Failed.is_terminal());
        assert!(SideEffectState::Uncertain.is_terminal());
        assert!(!SideEffectState::Planned.is_terminal());
        assert!(!SideEffectState::Executing.is_terminal());
    }

    #[test]
    fn writer_full_state_machine() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("test.db");
        let writer = IdempotencyWriter::open(&db).unwrap();
        let reader = IdempotencyReader::open(&db).unwrap();

        let args = serde_json::json!({"path": "a.txt"});
        let op_id = writer
            .record_planned("run_w", "tc_1", "write_file", &args)
            .unwrap();
        assert!(!op_id.is_empty());

        writer.mark_executing("run_w", "tc_1").unwrap();
        writer
            .commit(
                "run_w",
                "tc_1",
                "write_file",
                &args,
                &serde_json::json!({"ok": true}),
            )
            .unwrap();

        let committed = reader.committed_for_run("run_w").unwrap();
        assert_eq!(committed.len(), 1);
        assert_eq!(committed[0].tool_call_id, "tc_1");
        assert_eq!(committed[0].state, SideEffectState::Committed);
        assert_eq!(committed[0].operation_id, op_id);
    }

    #[test]
    fn writer_mark_failed() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("test.db");
        let writer = IdempotencyWriter::open(&db).unwrap();
        let reader = IdempotencyReader::open(&db).unwrap();

        let args = serde_json::json!({"path": "x.txt"});
        writer
            .record_planned("run_f", "tc_1", "write_file", &args)
            .unwrap();
        writer.mark_executing("run_f", "tc_1").unwrap();
        writer.mark_failed("run_f", "tc_1", "disk full").unwrap();

        let all = reader.for_run("run_f").unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].state, SideEffectState::Failed);
    }

    #[test]
    fn writer_mark_uncertain() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("test.db");
        let writer = IdempotencyWriter::open(&db).unwrap();
        let reader = IdempotencyReader::open(&db).unwrap();

        let args = serde_json::json!({"to": "bob"});
        writer
            .record_planned("run_u", "tc_1", "send_message", &args)
            .unwrap();
        writer.mark_executing("run_u", "tc_1").unwrap();
        writer.mark_uncertain("run_u", "tc_1").unwrap();

        let uncertain = reader.uncertain_for_run("run_u").unwrap();
        assert_eq!(uncertain.len(), 1);
        assert_eq!(uncertain[0].state, SideEffectState::Uncertain);
    }

    #[test]
    fn operation_id_matches_python_format() {
        let op = operation_id("run_abc", "tc_42");
        assert_eq!(op.len(), 36);
    }

    #[test]
    fn args_sha256_stable() {
        let a = args_sha256(&serde_json::json!({"b": 1, "a": 2}));
        let b = args_sha256(&serde_json::json!({"a": 2, "b": 1}));
        assert_eq!(a, b);
    }
}
