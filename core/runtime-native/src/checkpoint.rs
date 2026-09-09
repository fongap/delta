//! Checkpoint Authority (R2, ADR-029).
//!
//! This module provides the sole trusted authority for recovery checkpoints.
//! Checkpoints are persisted as `checkpoint.registered` events in the
//! run-event ledger (`run_events.db`), hash-chained and canonical.
//!
//! Contract: `docs/architecture/adr/ADR-029-r2-checkpoint-hard-cut.md`
//! and `core/recovery.py:RecoverySnapshot` (Python facade mirrors these fields).

use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use time::OffsetDateTime;
use uuid::Uuid;

use crate::ledger::{hex_encode_sha256, LedgerReader, LedgerWriter};
use crate::ShadowReadError;

/// Current checkpoint schema version.
pub const CHECKPOINT_SCHEMA_VERSION: i64 = 1;

/// Event type for checkpoint registration.
const EV_CHECKPOINT_REGISTERED: &str = "checkpoint.registered";

/// A checkpoint record — the canonical trusted fact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckpointRecord {
    pub id: String,
    pub run_id: String,
    pub session_id: String,
    pub schema: i64,
    pub created_at: String,
    pub phase: String,
    pub pending_tool_call: Option<Value>,
    pub pending_inbox_item_id: Option<String>,
    pub last_event_seq: Option<i64>,
    pub todo_summary: Vec<Value>,
    pub recent_artifacts: Vec<Value>,
    pub error: Option<String>,
    pub snapshot_hash: String,
    pub recoverable: bool,
}

impl CheckpointRecord {
    /// Compute canonical snapshot hash for integrity.
    /// Excludes metadata fields (created_at, id, snapshot_hash, recoverable)
    /// so that identical content produces the same hash regardless of write time.
    #[allow(clippy::too_many_arguments)]
    fn compute_hash(
        run_id: &str,
        session_id: &str,
        schema: i64,
        phase: &str,
        pending_tool_call: &Option<Value>,
        pending_inbox_item_id: &Option<String>,
        last_event_seq: &Option<i64>,
        todo_summary: &[Value],
        recent_artifacts: &[Value],
        error: &Option<String>,
    ) -> String {
        let canonical = serde_json::json!({
            "run_id": run_id,
            "session_id": session_id,
            "schema": schema,
            "phase": phase,
            "pending_tool_call": pending_tool_call,
            "pending_inbox_item_id": pending_inbox_item_id,
            "last_event_seq": last_event_seq,
            "todo_summary": todo_summary,
            "recent_artifacts": recent_artifacts,
            "error": error,
        });
        let canonical_str = canonical_json(&canonical);
        let mut hasher = Sha256::new();
        hasher.update(canonical_str.as_bytes());
        hex_encode_sha256(hasher.finalize().as_slice())
    }

    /// Determine if checkpoint is recoverable (paused on user action).
    fn compute_recoverable(phase: &str) -> bool {
        matches!(
            phase,
            "awaiting_approval" | "awaiting_question" | "awaiting_directory" | "awaiting_plan"
        )
    }
}

/// Read-only handle that replays checkpoint events from the ledger.
pub struct CheckpointReader {
    reader: LedgerReader,
}

impl CheckpointReader {
    /// Open the ledger DB in read-only mode.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, ShadowReadError> {
        Ok(Self {
            reader: LedgerReader::open(path)?,
        })
    }

    /// Wrap an existing LedgerReader.
    pub fn from_reader(reader: LedgerReader) -> Self {
        Self { reader }
    }

    /// Get a checkpoint by ID.
    pub fn get(&self, checkpoint_id: &str) -> Result<Option<CheckpointRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let checkpoints = self.replay_checkpoints(&events)?;
        Ok(checkpoints.into_iter().find(|c| c.id == checkpoint_id))
    }

    /// Get the latest checkpoint for a run_id.
    pub fn latest(&self, run_id: &str) -> Result<Option<CheckpointRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let checkpoints = self.replay_checkpoints(&events)?;
        let candidates: Vec<_> = checkpoints
            .into_iter()
            .filter(|c| c.run_id == run_id)
            .collect();
        if candidates.is_empty() {
            return Ok(None);
        }
        let latest = candidates
            .into_iter()
            .max_by_key(|c| c.created_at.clone())
            .unwrap();
        Ok(Some(latest))
    }

    /// List all checkpoints, optionally filtered by run_id / session_id.
    pub fn list(
        &self,
        run_id: Option<&str>,
        session_id: Option<&str>,
    ) -> Result<Vec<CheckpointRecord>, ShadowReadError> {
        let events = self.reader.all_events()?;
        let checkpoints = self.replay_checkpoints(&events)?;
        let filtered: Vec<_> = checkpoints
            .into_iter()
            .filter(|c| {
                run_id.is_none_or(|r| c.run_id == r) && session_id.is_none_or(|s| c.session_id == s)
            })
            .collect();
        Ok(filtered)
    }

    /// Validate a checkpoint record by ID (schema, hash, structure).
    pub fn validate(
        &self,
        checkpoint_id: &str,
    ) -> Result<CheckpointValidationResult, ShadowReadError> {
        match self.get(checkpoint_id)? {
            Some(cp) => {
                let expected_hash = CheckpointRecord::compute_hash(
                    &cp.run_id,
                    &cp.session_id,
                    cp.schema,
                    &cp.phase,
                    &cp.pending_tool_call,
                    &cp.pending_inbox_item_id,
                    &cp.last_event_seq,
                    &cp.todo_summary,
                    &cp.recent_artifacts,
                    &cp.error,
                );
                if expected_hash != cp.snapshot_hash {
                    return Ok(CheckpointValidationResult {
                        valid: false,
                        checkpoint_id: cp.id.clone(),
                        error_code: Some("integrity_mismatch".to_string()),
                        detail: Some(
                            "snapshot hash does not match recomputed canonical hash".to_string(),
                        ),
                    });
                }
                if cp.schema != CHECKPOINT_SCHEMA_VERSION {
                    return Ok(CheckpointValidationResult {
                        valid: false,
                        checkpoint_id: cp.id.clone(),
                        error_code: Some("unsupported_version".to_string()),
                        detail: Some(format!(
                            "schema {} != expected {}",
                            cp.schema, CHECKPOINT_SCHEMA_VERSION
                        )),
                    });
                }
                Ok(CheckpointValidationResult {
                    valid: true,
                    checkpoint_id: cp.id,
                    error_code: None,
                    detail: None,
                })
            }
            None => Ok(CheckpointValidationResult {
                valid: false,
                checkpoint_id: checkpoint_id.to_string(),
                error_code: Some("missing".to_string()),
                detail: Some("checkpoint not found".to_string()),
            }),
        }
    }

    fn replay_checkpoints(
        &self,
        events: &[crate::ledger::LedgerEvent],
    ) -> Result<Vec<CheckpointRecord>, ShadowReadError> {
        use std::collections::HashMap;
        let mut checkpoints: HashMap<String, CheckpointRecord> = HashMap::new();

        for ev in events {
            if ev.r#type == EV_CHECKPOINT_REGISTERED {
                let payload = &ev.payload;
                let id = payload
                    .get("id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        ShadowReadError::Parse("checkpoint.registered missing id".into())
                    })?
                    .to_string();

                let record = CheckpointRecord {
                    id: id.clone(),
                    run_id: payload
                        .get("run_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    session_id: payload
                        .get("session_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    schema: payload.get("schema").and_then(Value::as_i64).unwrap_or(0),
                    created_at: payload
                        .get("created_at")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    phase: payload
                        .get("phase")
                        .and_then(Value::as_str)
                        .unwrap_or("running")
                        .to_string(),
                    pending_tool_call: payload.get("pending_tool_call").cloned(),
                    pending_inbox_item_id: payload
                        .get("pending_inbox_item_id")
                        .and_then(Value::as_str)
                        .map(String::from),
                    last_event_seq: payload.get("last_event_seq").and_then(Value::as_i64),
                    todo_summary: payload
                        .get("todo_summary")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default(),
                    recent_artifacts: payload
                        .get("recent_artifacts")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default(),
                    error: payload
                        .get("error")
                        .and_then(Value::as_str)
                        .map(String::from),
                    snapshot_hash: payload
                        .get("snapshot_hash")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    recoverable: payload
                        .get("recoverable")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                };
                checkpoints.insert(id, record);
            }
        }

        let mut out: Vec<_> = checkpoints.into_values().collect();
        out.sort_by(|a, b| a.created_at.cmp(&b.created_at));
        Ok(out)
    }
}

/// Validation result for a checkpoint.
#[derive(Debug, Clone, Serialize)]
pub struct CheckpointValidationResult {
    pub valid: bool,
    pub checkpoint_id: String,
    pub error_code: Option<String>,
    pub detail: Option<String>,
}

/// Write authority for Checkpoint facts.
pub struct CheckpointWriter<'a> {
    ledger: &'a LedgerWriter,
}

impl<'a> CheckpointWriter<'a> {
    pub fn new(ledger: &'a LedgerWriter) -> Self {
        Self { ledger }
    }

    /// Register a new checkpoint (or re-register same id with same content = idempotent).
    pub fn register(
        &self,
        input: CheckpointRegisterInput,
        ts: f64,
        workspace: &str,
    ) -> Result<CheckpointRecord, ShadowReadError> {
        let checkpoint_id = input
            .checkpoint_id
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        let created_at = OffsetDateTime::now_utc()
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap_or_else(|_| OffsetDateTime::now_utc().to_string());

        let snapshot_hash = CheckpointRecord::compute_hash(
            &input.run_id,
            &input.session_id,
            CHECKPOINT_SCHEMA_VERSION,
            &input.phase,
            &input.pending_tool_call,
            &input.pending_inbox_item_id,
            &input.last_event_seq,
            &input.todo_summary,
            &input.recent_artifacts,
            &input.error,
        );

        let recoverable = CheckpointRecord::compute_recoverable(&input.phase);

        let record = CheckpointRecord {
            id: checkpoint_id.clone(),
            run_id: input.run_id.clone(),
            session_id: input.session_id.clone(),
            schema: CHECKPOINT_SCHEMA_VERSION,
            created_at: created_at.clone(),
            phase: input.phase.clone(),
            pending_tool_call: input.pending_tool_call.clone(),
            pending_inbox_item_id: input.pending_inbox_item_id.clone(),
            last_event_seq: input.last_event_seq,
            todo_summary: input.todo_summary.clone(),
            recent_artifacts: input.recent_artifacts.clone(),
            error: input.error.clone(),
            snapshot_hash: snapshot_hash.clone(),
            recoverable,
        };

        // Check for idempotent duplicate: same id + same hash = success
        // Different hash = conflict
        let existing = self.ledger.reader()?.events(&input.run_id)?;
        for ev in &existing {
            if ev.r#type == EV_CHECKPOINT_REGISTERED {
                let existing_id = ev.payload.get("id").and_then(Value::as_str).unwrap_or("");
                if existing_id == checkpoint_id {
                    let existing_hash = ev
                        .payload
                        .get("snapshot_hash")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    if existing_hash == snapshot_hash {
                        return Ok(record);
                    } else {
                        return Err(ShadowReadError::Parse(format!(
                            "checkpoint id conflict: {} has different content",
                            checkpoint_id
                        )));
                    }
                }
            }
        }

        self.append_checkpoint_registered(&record, ts, workspace)?;
        Ok(record)
    }

    fn append_checkpoint_registered(
        &self,
        record: &CheckpointRecord,
        ts: f64,
        workspace: &str,
    ) -> Result<(), ShadowReadError> {
        let payload = serde_json::json!({
            "id": record.id,
            "run_id": record.run_id,
            "session_id": record.session_id,
            "schema": record.schema,
            "created_at": record.created_at,
            "phase": record.phase,
            "pending_tool_call": record.pending_tool_call,
            "pending_inbox_item_id": record.pending_inbox_item_id,
            "last_event_seq": record.last_event_seq,
            "todo_summary": record.todo_summary,
            "recent_artifacts": record.recent_artifacts,
            "error": record.error,
            "snapshot_hash": record.snapshot_hash,
            "recoverable": record.recoverable,
        });
        self.ledger.append(
            &record.run_id,
            EV_CHECKPOINT_REGISTERED,
            "system",
            ts,
            &payload,
            workspace,
        )?;
        Ok(())
    }
}

/// Input for registering a checkpoint (from Python facade).
#[derive(Debug, Clone, Deserialize)]
pub struct CheckpointRegisterInput {
    pub checkpoint_id: Option<String>,
    pub run_id: String,
    pub session_id: String,
    pub phase: String,
    #[serde(default)]
    pub pending_tool_call: Option<Value>,
    #[serde(default)]
    pub pending_inbox_item_id: Option<String>,
    #[serde(default)]
    pub last_event_seq: Option<i64>,
    #[serde(default)]
    pub todo_summary: Vec<Value>,
    #[serde(default)]
    pub recent_artifacts: Vec<Value>,
    #[serde(default)]
    pub error: Option<String>,
}

/// Canonical JSON: sorted keys, compact separators.
/// Mirrors Ledger's canonical_json for cross-language consistency.
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tempfile::tempdir;

    fn sample_input() -> CheckpointRegisterInput {
        CheckpointRegisterInput {
            checkpoint_id: None,
            run_id: "run_1".to_string(),
            session_id: "sess_1".to_string(),
            phase: "awaiting_approval".to_string(),
            pending_tool_call: Some(
                json!({"id": "tc_1", "name": "send_message", "args_preview": "..."}),
            ),
            pending_inbox_item_id: Some("inbox_1".to_string()),
            last_event_seq: Some(42),
            todo_summary: vec![
                json!({"content": "do X", "status": "pending", "active_form": "doing X"}),
            ],
            recent_artifacts: vec![json!({"path": "out/x.md", "kind": "markdown"})],
            error: None,
        }
    }

    #[test]
    fn register_and_read_roundtrip() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let cp_writer = CheckpointWriter::new(&writer);

        let input = sample_input();
        let record = cp_writer.register(input, 1000.0, "ws_1").unwrap();

        let reader = CheckpointReader::open(&db).unwrap();
        let got = reader.get(&record.id).unwrap().unwrap();
        assert_eq!(got.id, record.id);
        assert_eq!(got.run_id, "run_1");
        assert_eq!(got.session_id, "sess_1");
        assert_eq!(got.phase, "awaiting_approval");
        assert!(got.recoverable);
        assert!(!got.snapshot_hash.is_empty());
    }

    #[test]
    fn idempotent_same_id_same_hash() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let cp_writer = CheckpointWriter::new(&writer);

        let input = sample_input();
        let id = "cp_fixed_id".to_string();
        let input1 = CheckpointRegisterInput {
            checkpoint_id: Some(id.clone()),
            ..input.clone()
        };
        let input2 = CheckpointRegisterInput {
            checkpoint_id: Some(id.clone()),
            ..input
        };

        let r1 = cp_writer.register(input1, 1000.0, "ws_1").unwrap();
        let r2 = cp_writer.register(input2, 1001.0, "ws_1").unwrap();
        assert_eq!(r1.id, r2.id);
        assert_eq!(r1.snapshot_hash, r2.snapshot_hash);
    }

    #[test]
    fn conflict_same_id_different_hash() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let cp_writer = CheckpointWriter::new(&writer);

        let id = "cp_conflict".to_string();
        let input1 = CheckpointRegisterInput {
            checkpoint_id: Some(id.clone()),
            phase: "awaiting_approval".to_string(),
            ..sample_input()
        };
        let input2 = CheckpointRegisterInput {
            checkpoint_id: Some(id.clone()),
            phase: "awaiting_question".to_string(),
            ..sample_input()
        };

        cp_writer.register(input1, 1000.0, "ws_1").unwrap();
        let err = cp_writer.register(input2, 1001.0, "ws_1");
        assert!(err.is_err());
        assert!(err.unwrap_err().to_string().contains("conflict"));
    }

    #[test]
    fn latest_by_run_id() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let cp_writer = CheckpointWriter::new(&writer);

        let mut input = sample_input();
        input.run_id = "run_latest".to_string();
        cp_writer
            .register(
                CheckpointRegisterInput {
                    checkpoint_id: Some("cp_1".into()),
                    ..input.clone()
                },
                1000.0,
                "ws_1",
            )
            .unwrap();
        cp_writer
            .register(
                CheckpointRegisterInput {
                    checkpoint_id: Some("cp_2".into()),
                    ..input
                },
                2000.0,
                "ws_1",
            )
            .unwrap();

        let reader = CheckpointReader::open(&db).unwrap();
        let latest = reader.latest("run_latest").unwrap().unwrap();
        assert_eq!(latest.id, "cp_2");
    }

    #[test]
    fn validate_ok() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let cp_writer = CheckpointWriter::new(&writer);

        let input = sample_input();
        let record = cp_writer.register(input, 1000.0, "ws_1").unwrap();

        let reader = CheckpointReader::open(&db).unwrap();
        let result = reader.validate(&record.id).unwrap();
        assert!(result.valid);
        assert!(result.error_code.is_none());
    }

    #[test]
    fn validate_missing() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        // Create the table first
        let _writer = LedgerWriter::open(&db).unwrap();
        let reader = CheckpointReader::open(&db).unwrap();
        let result = reader.validate("nonexistent").unwrap();
        assert!(!result.valid);
        assert_eq!(result.error_code, Some("missing".to_string()));
    }

    #[test]
    fn validate_wrong_schema() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();

        // Manually insert a wrong-schema checkpoint event with correct hash for its content
        // Use compute_hash directly (which uses canonical_json internally)
        let snapshot_hash = CheckpointRecord::compute_hash(
            "run_x",
            "sess_x",
            999,
            "running",
            &None,
            &None,
            &None,
            &[],
            &[],
            &None,
        );

        let payload_with_hash = json!({
            "id": "cp_bad_schema",
            "run_id": "run_x",
            "session_id": "sess_x",
            "schema": 999,
            "phase": "running",
            "pending_tool_call": null,
            "pending_inbox_item_id": null,
            "last_event_seq": null,
            "todo_summary": [],
            "recent_artifacts": [],
            "error": null,
            "snapshot_hash": snapshot_hash,
            "recoverable": false,
        });
        writer
            .append(
                "run_x",
                "checkpoint.registered",
                "system",
                1000.0,
                &payload_with_hash,
                "ws_1",
            )
            .unwrap();

        let reader = CheckpointReader::open(&db).unwrap();
        let result = reader.validate("cp_bad_schema").unwrap();
        assert!(!result.valid);
        assert_eq!(result.error_code, Some("unsupported_version".to_string()));
    }

    #[test]
    fn validate_integrity_mismatch() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();

        // Insert checkpoint with wrong hash
        writer
            .append(
                "run_x",
                "checkpoint.registered",
                "system",
                1000.0,
                &json!({
                    "id": "cp_bad_hash",
                    "run_id": "run_x",
                    "session_id": "sess_x",
                    "schema": CHECKPOINT_SCHEMA_VERSION,
                    "created_at": "2026-01-01T00:00:00Z",
                    "phase": "running",
                    "snapshot_hash": "wrong_hash_value",
                    "recoverable": false,
                }),
                "ws_1",
            )
            .unwrap();

        let reader = CheckpointReader::open(&db).unwrap();
        let result = reader.validate("cp_bad_hash").unwrap();
        assert!(!result.valid);
        assert_eq!(result.error_code, Some("integrity_mismatch".to_string()));
    }

    #[test]
    fn list_filter_by_run_and_session() {
        let dir = tempdir().unwrap();
        let db = dir.path().join("run-events.db");
        let writer = LedgerWriter::open(&db).unwrap();
        let cp_writer = CheckpointWriter::new(&writer);

        let input1 = sample_input();
        cp_writer.register(input1, 1000.0, "ws_1").unwrap();

        let mut input2 = sample_input();
        input2.run_id = "run_2".to_string();
        input2.session_id = "sess_2".to_string();
        cp_writer.register(input2, 2000.0, "ws_1").unwrap();

        let reader = CheckpointReader::open(&db).unwrap();
        let all = reader.list(None, None).unwrap();
        assert_eq!(all.len(), 2);

        let by_run = reader.list(Some("run_1"), None).unwrap();
        assert_eq!(by_run.len(), 1);
        assert_eq!(by_run[0].run_id, "run_1");

        let by_session = reader.list(None, Some("sess_2")).unwrap();
        assert_eq!(by_session.len(), 1);
        assert_eq!(by_session[0].session_id, "sess_2");
    }

    #[test]
    fn schema_version_constant() {
        assert_eq!(CHECKPOINT_SCHEMA_VERSION, 1);
    }
}
