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
        conn.execute_batch(
            r#"CREATE TABLE side_effects (
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
        conn.execute_batch("CREATE INDEX idx_side_effects_state ON side_effects(state, run_id)")?;
        Ok(Self { conn })
    }

    /// List all side effects for a run, ordered by updated_at.
    pub fn for_run(&self, run_id: &str) -> Result<Vec<SideEffectEntry>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
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

#[cfg(test)]
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
}
