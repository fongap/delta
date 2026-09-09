//! Read-only shadow access to the Python Runtime's run-event ledger.
//!
//! The Python `core/ledger.py` `RunEventLedger` writes hash-chained events
//! to a SQLite table `run_events`. This module opens the same DB file
//! read-only and verifies the chain from Rust.
//!
//! Contract: `docs/architecture/runtime-public-contract.md` §2.3.

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::ShadowReadError;

/// One row from the `run_events` table.
#[derive(Debug, Clone)]
pub struct LedgerEvent {
    pub run_id: String,
    pub seq: i64,
    pub r#type: String,
    pub ts: f64,
    pub actor: String,
    pub payload: Value,
    pub prev_hash: String,
    pub hash: String,
    pub workspace: String,
}

/// Read-only handle to a `run_events.db` file.
pub struct LedgerReader {
    conn: Connection,
    _owned: bool,
}

impl LedgerReader {
    /// Open the SQLite DB at `path` in read-only mode.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA query_only = ON;")?;
        Ok(Self { conn, _owned: true })
    }

    /// Wrap an existing connection for read-only access.
    ///
    /// Used by `LedgerWriter::reader()` to reuse the writer's connection
    /// for read operations without opening a second handle.
    pub fn from_connection(conn: Connection) -> Self {
        Self {
            conn,
            _owned: false,
        }
    }

    /// Open an in-memory DB (for tests).
    pub fn open_in_memory() -> Result<Self, ShadowReadError> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(
            r#"CREATE TABLE run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                ts REAL NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                payload TEXT NOT NULL DEFAULT '{}',
                prev_hash TEXT NOT NULL DEFAULT '',
                hash TEXT NOT NULL,
                workspace TEXT
            )"#,
        )?;
        conn.execute_batch("CREATE INDEX idx_run_events_run ON run_events(run_id, seq)")?;
        Ok(Self { conn, _owned: true })
    }

    /// List all events for a run, ordered by seq.
    pub fn events(&self, run_id: &str) -> Result<Vec<LedgerEvent>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, seq, type, ts, actor, payload, prev_hash, hash, workspace
             FROM run_events WHERE run_id = ? ORDER BY seq",
        )?;
        let rows = stmt.query_map(params![run_id], Self::map_event_row)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Map a SQLite row to a `LedgerEvent`.
    fn map_event_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<LedgerEvent> {
        let payload_str: String = row.get(5)?;
        let payload: Value = serde_json::from_str(&payload_str).unwrap_or(Value::Null);
        let workspace: Option<String> = row.get(8)?;
        Ok(LedgerEvent {
            run_id: row.get(0)?,
            seq: row.get(1)?,
            r#type: row.get(2)?,
            ts: row.get(3)?,
            actor: row.get(4)?,
            payload,
            prev_hash: row.get(6)?,
            hash: row.get(7)?,
            workspace: workspace.unwrap_or_default(),
        })
    }

    /// List all run_ids that have at least one event.
    pub fn runs(&self) -> Result<Vec<String>, ShadowReadError> {
        let mut stmt = self
            .conn
            .prepare("SELECT DISTINCT run_id FROM run_events ORDER BY rowid")?;
        let rows = stmt.query_map([], |row| row.get(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// List all events in the database regardless of run_id.
    /// Used by source/citation replay to read all source.registered
    /// and citation.marked events.
    pub fn all_events(&self) -> Result<Vec<LedgerEvent>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT run_id, seq, type, ts, actor, payload, prev_hash, hash, workspace
             FROM run_events ORDER BY rowid",
        )?;
        let rows = stmt.query_map([], Self::map_event_row)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// List events for a run filtered by workspace.
    ///
    /// Mirrors `core/ledger.py` `RunEventLedger.events_in_workspace()`.
    /// Empty `workspace` matches rows where the column is NULL or empty.
    pub fn events_in_workspace(
        &self,
        run_id: &str,
        workspace: &str,
    ) -> Result<Vec<LedgerEvent>, ShadowReadError> {
        let sql = if workspace.is_empty() {
            "SELECT run_id, seq, type, ts, actor, payload, prev_hash, hash, workspace \
             FROM run_events WHERE run_id = ? AND (workspace IS NULL OR workspace = '') \
             ORDER BY seq"
        } else {
            "SELECT run_id, seq, type, ts, actor, payload, prev_hash, hash, workspace \
             FROM run_events WHERE run_id = ? AND workspace = ? ORDER BY seq"
        };
        let mut stmt = self.conn.prepare(sql)?;
        let rows = if workspace.is_empty() {
            stmt.query_map(params![run_id], Self::map_event_row)?
        } else {
            stmt.query_map(params![run_id, workspace], Self::map_event_row)?
        };
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// List run_ids that have at least one event but no terminal event.
    ///
    /// Mirrors `core/ledger.py` `RunEventLedger.open_runs()`.
    pub fn open_runs(&self) -> Result<Vec<String>, ShadowReadError> {
        let mut stmt = self.conn.prepare(
            "SELECT DISTINCT run_id FROM run_events \
             WHERE run_id NOT IN ( \
                 SELECT run_id FROM run_events WHERE type IN \
                 ('run.completed', 'run.failed', 'run.interrupted', \
                  'run.skipped', 'run.cancelled') \
             )",
        )?;
        let rows = stmt.query_map([], |row| row.get(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Derive a run's lifecycle status from its last **terminal** event.
    ///
    /// Mirrors `core/ledger.py` `RunEventLedger.run_status()`.
    /// Scans events in reverse order for the first terminal event
    /// (run.completed / run.failed / run.interrupted / run.skipped /
    /// run.cancelled / validation.failed). Non-terminal events like
    /// tool.finished do not mask the terminal status.
    pub fn run_status(&self, run_id: &str) -> Result<String, ShadowReadError> {
        if run_id.is_empty() {
            return Ok("unknown".to_string());
        }
        // Scan all events in reverse order, find the first terminal event.
        let mut stmt = self
            .conn
            .prepare("SELECT type FROM run_events WHERE run_id = ? ORDER BY seq DESC")?;
        let rows = stmt.query_map(params![run_id], |row| row.get::<_, String>(0))?;
        let mut last_non_terminal = String::new();
        for row in rows {
            let event_type = row?;
            match event_type.as_str() {
                "run.completed" => return Ok("ok".to_string()),
                "run.failed" => return Ok("error".to_string()),
                "run.interrupted" => return Ok("interrupted".to_string()),
                "run.skipped" => return Ok("skipped".to_string()),
                "run.cancelled" => return Ok("cancelled".to_string()),
                "validation.failed" => return Ok("validation_failed".to_string()),
                // Non-terminal: remember the FIRST one found when scanning
                // backwards (which is the LAST event chronologically).
                _ => {
                    if last_non_terminal.is_empty() {
                        last_non_terminal = event_type;
                    }
                }
            }
        }
        // No terminal events found. map last non-terminal to running/resumed.
        match last_non_terminal.as_str() {
            "run.started" => Ok("running".to_string()),
            "run.resumed" => Ok("resumed".to_string()),
            _ => Ok("unknown".to_string()),
        }
    }

    /// Recompute the hash chain for one run; true iff every link matches.
    ///
    /// Mirrors `core/ledger.py` `RunEventLedger.verify()`:
    ///   basis = sha256("{prev_hash}|{seq}|{type}|{actor}|{ts_repr}|{canonical_payload}")
    pub fn verify(&self, run_id: &str) -> Result<bool, ShadowReadError> {
        let events = self.events(run_id)?;
        let mut prev = String::new();
        for ev in &events {
            let ts_repr = format_ts_repr(ev.ts);
            let canonical = canonical_json(&ev.payload);
            let basis = format!(
                "{}|{}|{}|{}|{}|{}",
                prev, ev.seq, ev.r#type, ev.actor, ts_repr, canonical
            );
            let digest = hex_encode_sha256(basis.as_bytes());
            if digest != ev.hash {
                return Ok(false);
            }
            if ev.prev_hash != prev {
                return Ok(false);
            }
            prev = ev.hash.clone();
        }
        Ok(true)
    }
}

/// Read-write handle to a `run_events.db` file.
///
/// Mirrors the Python `RunEventLedger.append()` write path. The payload is
/// expected to be pre-sanitized by the caller (the Python delegate scrubs
/// through `packages/sanitize.py` before forwarding), so the hash basis
/// matches what Python would compute.
pub struct LedgerWriter {
    db_path: PathBuf,
    conn: Connection,
}

impl LedgerWriter {
    /// Open (or create) a `run_events.db` for read-write access.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        let path = path.as_ref().to_path_buf();
        let conn = Connection::open(&path)?;
        conn.execute_batch(
            r#"CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                ts REAL NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                payload TEXT NOT NULL DEFAULT '{}',
                prev_hash TEXT NOT NULL DEFAULT '',
                hash TEXT NOT NULL,
                workspace TEXT
            )"#,
        )?;
        conn.execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq)",
        )?;
        // Workspace index migration (ADR-007 §10.6). Idempotent: ALTER TABLE
        // fails silently on subsequent boots when the column already exists.
        for ddl in &[
            "ALTER TABLE run_events ADD COLUMN workspace TEXT",
            "CREATE INDEX IF NOT EXISTS idx_run_events_workspace ON run_events(workspace, run_id, seq)",
        ] {
            let _ = conn.execute_batch(ddl);
        }
        Ok(Self {
            db_path: path,
            conn,
        })
    }

    /// Open an in-memory DB (for tests).
    pub fn open_in_memory() -> Result<Self, ShadowReadError> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(
            r#"CREATE TABLE run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                ts REAL NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                payload TEXT NOT NULL DEFAULT '{}',
                prev_hash TEXT NOT NULL DEFAULT '',
                hash TEXT NOT NULL,
                workspace TEXT
            )"#,
        )?;
        conn.execute_batch("CREATE INDEX idx_run_events_run ON run_events(run_id, seq)")?;
        Ok(Self {
            conn,
            db_path: PathBuf::new(),
        })
    }

    /// Append one event, extending the run's hash chain.
    ///
    /// Mirrors `core/ledger.py` `RunEventLedger.append()`. ``payload`` is
    /// expected pre-sanitized by the caller. Returns the stored row as a
    /// JSON object.
    pub fn append(
        &self,
        run_id: &str,
        r#type: &str,
        actor: &str,
        ts: f64,
        payload: &Value,
        workspace: &str,
    ) -> Result<Value, ShadowReadError> {
        let payload_str = canonical_json(payload);
        let ts_repr = format_ts_repr(ts);

        let row = self
            .conn
            .query_row(
                "SELECT seq, hash FROM run_events WHERE run_id = ? \
                 ORDER BY seq DESC LIMIT 1",
                params![run_id],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
            )
            .ok();
        let (seq, prev_hash) = match row {
            Some((s, h)) => (s + 1, h),
            None => (1, String::new()),
        };
        let basis = format!(
            "{}|{}|{}|{}|{}|{}",
            prev_hash, seq, r#type, actor, ts_repr, payload_str
        );
        let digest = hex_encode_sha256(basis.as_bytes());
        self.conn.execute(
            "INSERT INTO run_events \
             (run_id, seq, type, ts, actor, payload, prev_hash, hash, workspace) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                run_id,
                seq,
                r#type,
                ts,
                actor,
                payload_str,
                &prev_hash,
                &digest,
                workspace,
            ],
        )?;
        Ok(serde_json::json!({
            "run_id": run_id,
            "seq": seq,
            "type": r#type,
            "ts": ts,
            "actor": actor,
            "payload": payload,
            "prev_hash": prev_hash,
            "hash": digest,
            "workspace": workspace,
        }))
    }

    /// Return a `LedgerReader` backed by a read-only connection to the same DB.
    ///
    /// Opens a separate read-only handle so reads don't block writes.
    pub fn reader(&self) -> Result<LedgerReader, ShadowReadError> {
        LedgerReader::open(&self.db_path)
    }

    /// Cold-start sweep: close every open run with a synthetic interrupted event.
    ///
    /// Mirrors `core/ledger.py` `RunEventLedger.recover_stale()`.
    /// Returns the list of synthetic events that were appended.
    pub fn recover_stale(&self) -> Result<Vec<Value>, ShadowReadError> {
        let reader = self.reader()?;
        let open_run_ids = reader.open_runs()?;
        let mut recovered = Vec::new();
        for run_id in &open_run_ids {
            let events = reader.events(run_id)?;
            let last_seq = events.last().map(|e| e.seq).unwrap_or(0);
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            let row = self.append(
                run_id,
                "run.interrupted",
                "system",
                ts,
                &serde_json::json!({"reason": "crashed", "last_event_seq": last_seq}),
                "",
            )?;
            recovered.push(row);
        }
        Ok(recovered)
    }
}

/// Python `repr(float)` for the hash basis.
///
/// `core/ledger.py` uses `repr(ts)` where ts is a Python float.
/// For timestamps written via `time.time()`, repr produces e.g.
/// `"1725523456.123456"`. We match this by formatting the f64
/// without scientific notation, stripping trailing zeros after the
/// decimal point but keeping at least one decimal digit.
fn format_ts_repr(ts: f64) -> String {
    if ts == ts.trunc() {
        format!("{:.1}", ts)
    } else {
        let s = format!("{}", ts);
        s
    }
}

/// Canonical JSON: sorted keys, compact separators, no trailing whitespace.
///
/// Mirrors `core/ledger.py` `_canonical()`:
///   json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
fn canonical_json(value: &Value) -> String {
    // serde_json does not sort keys by default. We recursively serialize
    // with sorted keys by walking the Value tree and producing a string
    // manually. This is not the fastest path, but correctness matters more
    // than speed for a shadow-read verification.
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
                buf.push_str(&escape_json_string(k));
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
            buf.push_str(&escape_json_string(s));
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

fn escape_json_string(s: &str) -> String {
    let mut buf = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '"' => buf.push_str("\\\""),
            '\\' => buf.push_str("\\\\"),
            '\x08' => buf.push_str("\\b"),
            '\x0c' => buf.push_str("\\f"),
            '\n' => buf.push_str("\\n"),
            '\r' => buf.push_str("\\r"),
            '\t' => buf.push_str("\\t"),
            c if c < '\x20' => {
                buf.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => buf.push(c),
        }
    }
    buf
}

pub fn hex_encode_sha256(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    let result = hasher.finalize();
    result.iter().map(|b| format!("{:02x}", b)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_db_has_no_runs() {
        let reader = LedgerReader::open_in_memory().unwrap();
        assert!(reader.runs().unwrap().is_empty());
    }

    #[test]
    fn verify_empty_run_is_true() {
        let reader = LedgerReader::open_in_memory().unwrap();
        assert!(reader.verify("nonexistent").unwrap());
    }

    #[test]
    fn canonical_json_sorted_keys() {
        let json = serde_json::json!({"b": 1, "a": 2, "c": 3});
        let result = canonical_json(&json);
        assert_eq!(result, r#"{"a":2,"b":1,"c":3}"#);
    }

    #[test]
    fn canonical_json_nested() {
        let json = serde_json::json!({"z": {"y": 2, "x": 1}, "a": [3, 2, 1]});
        let result = canonical_json(&json);
        assert!(result.contains(r#""x":1"#));
        assert!(result.contains(r#""y":2"#));
    }

    #[test]
    fn format_ts_repr_integer_float() {
        assert_eq!(format_ts_repr(1000.0), "1000.0");
    }

    #[test]
    fn format_ts_repr_fractional() {
        let s = format_ts_repr(1725523456.123456);
        assert!(s.contains("1725523456"));
    }

    #[test]
    fn writer_appends_first_event() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("ledger.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let row = writer
            .append(
                "run_w",
                "run.started",
                "user",
                1000.0,
                &serde_json::json!({"kind": "run"}),
                "",
            )
            .unwrap();
        assert_eq!(row["seq"], 1);
        assert_eq!(row["prev_hash"], "");
        assert!(!row["hash"].as_str().unwrap().is_empty());
    }

    #[test]
    fn writer_appends_extends_chain() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("ledger.db");
        let writer = LedgerWriter::open(&db).unwrap();
        writer
            .append(
                "run_w",
                "run.started",
                "user",
                1000.0,
                &serde_json::json!({"kind": "run"}),
                "",
            )
            .unwrap();
        let row2 = writer
            .append(
                "run_w",
                "run.completed",
                "system",
                1001.0,
                &serde_json::json!({"kind": "run"}),
                "",
            )
            .unwrap();
        assert_eq!(row2["seq"], 2);
        assert!(!row2["prev_hash"].as_str().unwrap().is_empty());
    }

    #[test]
    fn writer_chain_verifies_via_reader() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("ledger.db");
        let writer = LedgerWriter::open(&db).unwrap();
        writer
            .append(
                "run_v",
                "run.started",
                "user",
                1000.0,
                &serde_json::json!({"kind": "run"}),
                "ws_1",
            )
            .unwrap();
        writer
            .append(
                "run_v",
                "run.completed",
                "system",
                1001.0,
                &serde_json::json!({"kind": "run"}),
                "ws_1",
            )
            .unwrap();

        let reader = LedgerReader::open(&db).unwrap();
        let events = reader.events("run_v").unwrap();
        assert_eq!(events.len(), 2);
        assert!(reader.verify("run_v").unwrap());
    }

    #[test]
    fn writer_independent_runs_have_separate_chains() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let db = dir.path().join("ledger.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let r1 = writer
            .append(
                "run_a",
                "run.started",
                "user",
                1000.0,
                &serde_json::json!({}),
                "",
            )
            .unwrap();
        let r2 = writer
            .append(
                "run_b",
                "run.started",
                "user",
                1000.0,
                &serde_json::json!({}),
                "",
            )
            .unwrap();
        // Each run is its own chain, so both start with empty prev_hash.
        assert_eq!(r1["prev_hash"], "");
        assert_eq!(r2["prev_hash"], "");
    }
}
