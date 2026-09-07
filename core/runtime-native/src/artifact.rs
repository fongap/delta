//! Read-only shadow access to the Python Runtime's artifact registry (R2, ADR-019).
//!
//! Artifacts are produced by tool calls during a run and recorded in
//! the run-event ledger as `artifact.registered` / `artifact.completed`
//! events. The on-disk files live under the workspace and are
//! identified by their sha256.
//!
//! This reader opens the same ledger DB read-only, walks the artifact
//! events for a given run, and recomputes the on-disk sha256 to
//! cross-check that the recorded sha256 still matches the file content.
//! Mismatches are surfaced as `ArtifactMismatch` items; they do not
//! raise — Python remains the write authority, and a mismatch is
//! information for the Python caller, not a hard failure.
//!
//! Contract: ``docs/architecture/adr/ADR-005-reliable-task-runtime.md``
//! (WS2: Artifact domain) and ``docs/architecture/adr/ADR-019-r2-pre-plumbing.md``.

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::Value;

use crate::ShadowReadError;

/// One artifact as recorded in the ledger's `artifact.registered` payload.
#[derive(Debug, Clone)]
pub struct ArtifactRecord {
    pub path: String,
    pub kind: String,
    pub size: i64,
    pub sha256: String,
    pub run_id: String,
    pub incomplete: bool,
}

/// A discrepancy between the recorded sha256 and the on-disk file content.
#[derive(Debug, Clone)]
pub struct ArtifactMismatch {
    pub path: String,
    pub recorded_sha256: String,
    pub on_disk_sha256: Option<String>,
    pub reason: String,
}

/// Read-only handle to the artifact view of a run-event ledger.
pub struct ArtifactReader {
    conn: Connection,
}

impl ArtifactReader {
    /// Open the SQLite DB at `path` in read-only mode.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA query_only = ON;")?;
        Ok(Self { conn })
    }

    /// Test-only: wrap an already-open connection. The caller is
    /// responsible for the connection's lifecycle and the `run_events`
    /// schema being present.
    #[cfg(test)]
    pub(crate) fn open_in_memory_for_test(conn: Connection) -> Self {
        Self { conn }
    }

    /// List all artifacts recorded for `run_id` in registration order.
    pub fn list_for_run(&self, run_id: &str) -> Result<Vec<ArtifactRecord>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT payload FROM run_events \
             WHERE run_id = ?1 AND type = 'artifact.registered' \
             ORDER BY seq ASC",
        )?;
        let rows = stmt.query_map(params![run_id], |row| {
            let payload: String = row.get(0)?;
            Ok(payload)
        })?;
        let mut out = Vec::new();
        for row in rows {
            let payload: String = row?;
            let v: Value = serde_json::from_str(&payload)
                .map_err(|e| ShadowReadError::Parse(format!("artifact.registered payload: {e}")))?;
            let path = v
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let kind = v
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let size = v.get("size").and_then(Value::as_i64).unwrap_or(0);
            let sha256 = v
                .get("sha256")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let incomplete = v
                .get("incomplete")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            out.push(ArtifactRecord {
                path,
                kind,
                size,
                sha256,
                run_id: run_id.to_string(),
                incomplete,
            });
        }
        Ok(out)
    }

    /// Recompute sha256 for every recorded artifact against the files
    /// in `workspace` and return the list of mismatches (empty list
    /// means the ledger is consistent with the workspace).
    pub fn verify_against_workspace(
        &self,
        run_id: &str,
        workspace: &Path,
    ) -> Result<Vec<ArtifactMismatch>, ShadowReadError> {
        let records = self.list_for_run(run_id)?;
        let mut mismatches = Vec::new();
        for r in &records {
            if r.incomplete || r.sha256.is_empty() {
                continue;
            }
            let abs = workspace.join(&r.path);
            match sha256_file(&abs) {
                Ok(disk_sha) if disk_sha == r.sha256 => {}
                Ok(disk_sha) => mismatches.push(ArtifactMismatch {
                    path: r.path.clone(),
                    recorded_sha256: r.sha256.clone(),
                    on_disk_sha256: Some(disk_sha),
                    reason: "sha256 mismatch".to_string(),
                }),
                Err(e) => mismatches.push(ArtifactMismatch {
                    path: r.path.clone(),
                    recorded_sha256: r.sha256.clone(),
                    on_disk_sha256: None,
                    reason: format!("read failed: {e}"),
                }),
            }
        }
        Ok(mismatches)
    }
}

fn sha256_file(path: &PathBuf) -> std::io::Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::{params, Connection};

    /// Build an in-memory ledger with the same schema as `run_events.db`.
    fn new_test_ledger() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                ts REAL NOT NULL DEFAULT 0.0,
                payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL DEFAULT '',
                hash TEXT NOT NULL DEFAULT '',
                workspace TEXT NOT NULL DEFAULT ''
            )",
        )
        .unwrap();
        conn
    }

    fn insert_event(conn: &Connection, run_id: &str, seq: i64, event_type: &str, payload: &str) {
        conn.execute(
            "INSERT INTO run_events (run_id, seq, type, payload) VALUES (?1, ?2, ?3, ?4)",
            params![run_id, seq, event_type, payload],
        )
        .unwrap();
    }

    #[test]
    fn empty_run_returns_no_artifacts() {
        let conn = new_test_ledger();
        let r = ArtifactReader::open_in_memory_for_test(conn);
        let list = r.list_for_run("run_1").unwrap();
        assert!(list.is_empty());
    }

    #[test]
    fn list_filters_by_run_id() {
        let conn = new_test_ledger();
        insert_event(
            &conn,
            "run_1",
            1,
            "artifact.registered",
            r#"{"path":"a.md","kind":"markdown","size":10,"sha256":"abc","incomplete":false}"#,
        );
        insert_event(
            &conn,
            "run_2",
            1,
            "artifact.registered",
            r#"{"path":"b.md","kind":"markdown","size":20,"sha256":"def","incomplete":false}"#,
        );
        insert_event(&conn, "run_1", 2, "run.started", r#"{"kind":"started"}"#);
        let r = ArtifactReader::open_in_memory_for_test(conn);
        let list = r.list_for_run("run_1").unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].path, "a.md");
        assert_eq!(list[0].sha256, "abc");
    }

    #[test]
    fn verify_against_workspace_matches() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        std::fs::write(ws.join("a.md"), "hello world").unwrap();
        let expected = sha256_file(&ws.join("a.md")).unwrap();

        let conn = new_test_ledger();
        let payload = format!(
            r#"{{"path":"a.md","kind":"markdown","size":11,"sha256":"{expected}","incomplete":false}}"#
        );
        insert_event(&conn, "run_1", 1, "artifact.registered", &payload);
        let r = ArtifactReader::open_in_memory_for_test(conn);

        let mismatches = r.verify_against_workspace("run_1", ws).unwrap();
        assert!(
            mismatches.is_empty(),
            "expected no mismatches, got {mismatches:?}"
        );
    }

    #[test]
    fn verify_against_workspace_detects_tampering() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        std::fs::write(ws.join("a.md"), "goodbye world").unwrap();

        let conn = new_test_ledger();
        let payload =
            r#"{"path":"a.md","kind":"markdown","size":13,"sha256":"deadbeef","incomplete":false}"#;
        insert_event(&conn, "run_1", 1, "artifact.registered", payload);
        let r = ArtifactReader::open_in_memory_for_test(conn);

        let mismatches = r.verify_against_workspace("run_1", ws).unwrap();
        assert_eq!(mismatches.len(), 1);
        assert_eq!(mismatches[0].path, "a.md");
        assert_eq!(mismatches[0].reason, "sha256 mismatch");
    }

    #[test]
    fn verify_skips_incomplete_artifacts() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let ws = dir.path();
        // No on-disk file; the artifact is marked incomplete so verify
        // should skip it (no false-positive mismatch).
        let conn = new_test_ledger();
        let payload = r#"{"path":"a.md","kind":"markdown","size":0,"sha256":"","incomplete":true}"#;
        insert_event(&conn, "run_1", 1, "artifact.registered", payload);
        let r = ArtifactReader::open_in_memory_for_test(conn);

        let mismatches = r.verify_against_workspace("run_1", ws).unwrap();
        assert!(mismatches.is_empty());
    }
}
