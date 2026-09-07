//! Artifact registry: shadow reader (ADR-019) + Rust write authority (ADR-020).
//!
//! Artifacts are produced by tool calls during a run and recorded in
//! the run-event ledger as `artifact.registered` / `artifact.completed`
//! events. The on-disk files live under the workspace and are
//! identified by their sha256.
//!
//! Two surfaces in this module:
//!
//! - :class:`ArtifactReader` (read-only) — opens the same ledger DB
//!   read-only, walks the artifact events for a given run, and
//!   recomputes the on-disk sha256 to cross-check that the recorded
//!   sha256 still matches the file content. Mismatches are surfaced
//!   as `ArtifactMismatch` items; they do not raise — a mismatch is
//!   information for the Python caller, not a hard failure.
//!
//! - :class:`ArtifactRegistryWriter` (write authority) — wraps a
//!   `LedgerWriter` and appends `artifact.registered` (with the full
//!   `Artifact.to_dict()` payload) and `artifact.completed` (with the
//!   trimmed `path` / `sha256` / `size` payload) events. Activated
//!   when ``DELTA_RUST_AUTHORITY=artifact`` is set (PR132 / ADR-020).
//!
//! Contract: ``docs/architecture/adr/ADR-005-reliable-task-runtime.md``
//! (WS2: Artifact domain), ``docs/architecture/adr/ADR-019-r2-pre-plumbing.md``,
//! and ``docs/architecture/adr/ADR-020-r2-artifact-authority-switch.md``.

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::{json, Value};

use crate::{LedgerWriter, ShadowReadError};

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

// =============================================================================
// Write authority (PR132 / ADR-020)
// =============================================================================

/// Input for a single artifact registration. Mirrors the Python
/// `Artifact` dataclass (the fields the Rust side actually needs to
/// build the two ledger events).
#[derive(Debug, Clone)]
pub struct ArtifactInput {
    pub path: String,
    pub name: String,
    pub kind: String,
    pub size: i64,
    pub modified_at: f64,
    pub run_id: String,
    pub sha256: String,
    pub incomplete: bool,
    pub registered_at: f64,
}

impl ArtifactInput {
    /// Build the full payload for the `artifact.registered` event.
    /// Matches `Artifact.to_dict()` field-by-field.
    pub fn registered_payload(&self) -> Value {
        json!({
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "modified_at": self.modified_at,
            "run_id": self.run_id,
            "sha256": self.sha256,
            "incomplete": self.incomplete,
            "registered_at": self.registered_at,
        })
    }

    /// Build the trimmed payload for the `artifact.completed` event.
    /// Matches the Python `register_artifact` second-event shape.
    pub fn completed_payload(&self) -> Value {
        json!({
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
        })
    }
}

/// Result of a single `register_artifact` call: the two stored events
/// (or `None` for the second one if the artifact is incomplete).
#[derive(Debug, Clone)]
pub struct ArtifactRegistrationResult {
    pub registered: Value,
    pub completed: Option<Value>,
}

/// Write authority for the artifact registry. Wraps a `LedgerWriter`
/// and appends `artifact.registered` + (optionally) `artifact.completed`
/// events that mirror `core/artifact.py:register_artifact`.
///
/// The ledger hash chain is maintained by `LedgerWriter` — this
/// writer does not compute hashes directly. It only decides which
/// events to append and in what shape.
pub struct ArtifactRegistryWriter<'a> {
    ledger: &'a LedgerWriter,
}

impl<'a> ArtifactRegistryWriter<'a> {
    /// Wrap a borrowed `LedgerWriter`. The caller is responsible for
    /// the writer's lifecycle (typically: one per `delta_core` per-DB
    /// cache entry, matching the R1 pattern).
    pub fn new(ledger: &'a LedgerWriter) -> Self {
        Self { ledger }
    }

    /// Register one artifact. Mirrors the second half of
    /// `core/artifact.py:register_artifact`: appends
    /// `artifact.registered` and, if not incomplete, also
    /// `artifact.completed`. Both events use the run's actor =
    /// `"system"`. Workspace is forwarded to `LedgerWriter` so the
    /// event is grouped with the rest of the run.
    ///
    /// `ts` is the event timestamp; pass `0.0` to let the receiver
    /// default to now (Python `RunEventLedger.append` uses the
    /// `time.time()` default when `ts` is omitted).
    pub fn register(
        &self,
        artifact: &ArtifactInput,
        ts: f64,
        workspace: &str,
    ) -> Result<ArtifactRegistrationResult, ShadowReadError> {
        let registered = self.ledger.append(
            &artifact.run_id,
            "artifact.registered",
            "system",
            ts,
            &artifact.registered_payload(),
            workspace,
        )?;
        let completed = if !artifact.incomplete {
            Some(self.ledger.append(
                &artifact.run_id,
                "artifact.completed",
                "system",
                ts,
                &artifact.completed_payload(),
                workspace,
            )?)
        } else {
            None
        };
        Ok(ArtifactRegistrationResult {
            registered,
            completed,
        })
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

    // -- ArtifactRegistryWriter tests (PR132 / ADR-020) --------------------

    fn sample_artifact() -> ArtifactInput {
        ArtifactInput {
            path: "out/x.md".to_string(),
            name: "x.md".to_string(),
            kind: "markdown".to_string(),
            size: 100,
            modified_at: 1_700_000_000.0,
            run_id: "run_1".to_string(),
            sha256: "abc123".to_string(),
            incomplete: false,
            registered_at: 1_700_000_001.0,
        }
    }

    #[test]
    fn registered_payload_matches_artifact_to_dict_shape() {
        let a = sample_artifact();
        let p = a.registered_payload();
        assert_eq!(p["path"], "out/x.md");
        assert_eq!(p["name"], "x.md");
        assert_eq!(p["kind"], "markdown");
        assert_eq!(p["size"], 100);
        assert_eq!(p["modified_at"], 1_700_000_000.0);
        assert_eq!(p["run_id"], "run_1");
        assert_eq!(p["sha256"], "abc123");
        assert_eq!(p["incomplete"], false);
        assert_eq!(p["registered_at"], 1_700_000_001.0);
    }

    #[test]
    fn completed_payload_is_trimmed() {
        let a = sample_artifact();
        let p = a.completed_payload();
        // Only path / sha256 / size — no extra fields, matching the
        // Python register_artifact second-event shape.
        assert_eq!(p["path"], "out/x.md");
        assert_eq!(p["sha256"], "abc123");
        assert_eq!(p["size"], 100);
        assert!(p.get("kind").is_none());
        assert!(p.get("name").is_none());
        assert!(p.get("incomplete").is_none());
    }

    #[test]
    fn writer_appends_both_events_for_complete_artifact() {
        use crate::LedgerWriter;
        let ledger = LedgerWriter::open_in_memory().unwrap();
        let writer = ArtifactRegistryWriter::new(&ledger);
        let a = sample_artifact();
        let result = writer.register(&a, 1_700_000_002.0, "/workspace").unwrap();
        assert!(result.completed.is_some());
        assert_eq!(result.registered["type"], "artifact.registered");
        assert_eq!(result.completed.unwrap()["type"], "artifact.completed");
    }

    #[test]
    fn writer_skips_completed_for_incomplete_artifact() {
        use crate::LedgerWriter;
        let ledger = LedgerWriter::open_in_memory().unwrap();
        let writer = ArtifactRegistryWriter::new(&ledger);
        let mut a = sample_artifact();
        a.incomplete = true;
        a.sha256 = String::new();
        let result = writer.register(&a, 1_700_000_002.0, "/workspace").unwrap();
        // The registered event still fires (so the run can report
        // "artifact present but not yet readable" — matches Python).
        assert_eq!(result.registered["type"], "artifact.registered");
        // The completed event is suppressed for incomplete writes.
        assert!(result.completed.is_none());
    }
}
