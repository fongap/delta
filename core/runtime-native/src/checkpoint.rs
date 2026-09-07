//! Read-only shadow parser for recovery checkpoints (R2, ADR-019).
//!
//! Checkpoints are JSON snapshots written by
//! ``core/recovery.py:RecoveryStore``. This reader opens the snapshot
//! file (or the embedded ``recovery`` row in a session/conversations
//! table) and verifies its schema + structural validity. The reader
//! is intentionally strict about unknown future fields: a snapshot
//! with a higher ``schema`` version than the reader knows is
//! rejected, so callers can fall back to "no snapshot" semantics
//! rather than silently misinterpret.
//!
//! Contract: ``docs/architecture/adr/ADR-019-r2-pre-plumbing.md`` and
//! ``core/recovery.py:RecoverySnapshot``.
//!
//! Note: the Python ``RecoveryStore`` stores snapshots as a JSON string
//! column in ``core/conversations.db`` (table ``recovery_snapshots``).
//! This module operates on the raw JSON, not the DB, because the
//! column layout may evolve separately from the snapshot schema.

use std::path::Path;

use serde_json::Value;

/// Current snapshot schema version. Mirrors ``core/recovery.py:SCHEMA_VERSION``.
pub const SNAPSHOT_SCHEMA_VERSION: i64 = 1;

/// A parsed snapshot. Mirrors ``core/recovery.py:RecoverySnapshot`` fields
/// (the reader ignores fields it doesn't know; the writer is the source
/// of truth for what fields exist).
#[derive(Debug, Clone)]
pub struct ParsedSnapshot {
    pub schema: i64,
    pub snapshot_at: String,
    pub run_id: String,
    pub session_id: String,
    pub phase: String,
    pub last_event_seq: Option<i64>,
    pub pending_tool_call: Option<Value>,
    pub pending_inbox_item_id: Option<String>,
    pub todo_count: usize,
    pub recent_artifact_count: usize,
    pub error: Option<String>,
}

/// Read a snapshot from a JSON file on disk.
pub fn read_snapshot_file(path: &Path) -> Result<ParsedSnapshot, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("read: {e}"))?;
    let v: Value = serde_json::from_str(&text).map_err(|e| format!("json: {e}"))?;
    parse_snapshot(&v)
}

/// Parse a snapshot from a JSON value (in-memory cross-check path).
pub fn parse_snapshot(v: &Value) -> Result<ParsedSnapshot, String> {
    let obj = match v.as_object() {
        Some(o) => o,
        None => return Err("snapshot must be a JSON object".to_string()),
    };
    let schema = obj.get("schema").and_then(Value::as_i64).unwrap_or(0);
    if schema != SNAPSHOT_SCHEMA_VERSION {
        return Err(format!(
            "unsupported recovery snapshot schema: {schema} (expected {SNAPSHOT_SCHEMA_VERSION})"
        ));
    }
    let phase = obj
        .get("phase")
        .and_then(Value::as_str)
        .unwrap_or("running")
        .to_string();
    if !["running", "paused", "completed", "failed"].contains(&phase.as_str()) {
        return Err(format!("unknown phase: {phase:?}"));
    }
    let todos = obj.get("todo_summary").and_then(Value::as_array);
    let arts = obj.get("recent_artifacts").and_then(Value::as_array);
    Ok(ParsedSnapshot {
        schema,
        snapshot_at: obj
            .get("snapshot_at")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        run_id: obj
            .get("run_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        session_id: obj
            .get("session_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        phase,
        last_event_seq: obj.get("last_event_seq").and_then(Value::as_i64),
        pending_tool_call: obj.get("pending_tool_call").cloned(),
        pending_inbox_item_id: obj
            .get("pending_inbox_item_id")
            .and_then(Value::as_str)
            .map(String::from),
        todo_count: todos.map(|a| a.len()).unwrap_or(0),
        recent_artifact_count: arts.map(|a| a.len()).unwrap_or(0),
        error: obj.get("error").and_then(Value::as_str).map(String::from),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn sample_snapshot() -> Value {
        json!({
            "schema": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_at": "2026-09-07T00:00:00Z",
            "run_id": "run_1",
            "session_id": "sess_1",
            "phase": "running",
            "last_event_seq": 42,
            "pending_tool_call": {"id": "tc_1", "name": "send_message"},
            "pending_inbox_item_id": null,
            "todo_summary": [{"content": "do X", "status": "pending", "active_form": "doing X"}],
            "recent_artifacts": [{"path": "out/x.md", "kind": "markdown"}],
            "error": null,
        })
    }

    #[test]
    fn parse_full_snapshot() {
        let s = parse_snapshot(&sample_snapshot()).unwrap();
        assert_eq!(s.schema, SNAPSHOT_SCHEMA_VERSION);
        assert_eq!(s.run_id, "run_1");
        assert_eq!(s.phase, "running");
        assert_eq!(s.last_event_seq, Some(42));
        assert_eq!(s.todo_count, 1);
        assert_eq!(s.recent_artifact_count, 1);
        assert!(s.error.is_none());
    }

    #[test]
    fn parse_minimal_snapshot() {
        let v = json!({"schema": SNAPSHOT_SCHEMA_VERSION, "phase": "paused"});
        let s = parse_snapshot(&v).unwrap();
        assert_eq!(s.phase, "paused");
        assert_eq!(s.todo_count, 0);
        assert!(s.last_event_seq.is_none());
    }

    #[test]
    fn parse_rejects_wrong_schema() {
        let mut v = sample_snapshot();
        v.as_object_mut()
            .unwrap()
            .insert("schema".to_string(), json!(999));
        assert!(parse_snapshot(&v).is_err());
    }

    #[test]
    fn parse_rejects_unknown_phase() {
        let mut v = sample_snapshot();
        v.as_object_mut()
            .unwrap()
            .insert("phase".to_string(), json!("napping"));
        assert!(parse_snapshot(&v).is_err());
    }

    #[test]
    fn parse_rejects_non_object() {
        assert!(parse_snapshot(&json!("not an object")).is_err());
        assert!(parse_snapshot(&json!(42)).is_err());
    }

    #[test]
    fn snapshot_schema_version_constant() {
        // Pin the schema version so future migrations are intentional.
        assert_eq!(SNAPSHOT_SCHEMA_VERSION, 1);
    }
}
