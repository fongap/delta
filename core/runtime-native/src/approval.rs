//! Approval Authority (R2, ADR-031).
//!
//! This module provides the sole trusted authority for live approval decisions
//! and approval audit recording. Runtime workers register a pending request;
//! Tauri IPC resolves it through [`ApprovalController`]. No worker or provider
//! can mint its own approval.
//!
//! Contract: docs/architecture/adr/ADR-031-r2-approval-hard-cut.md
//! and core/audit.py (Python facade mirrors this).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{mpsc, Mutex};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ShadowReadError;

/// Current approval schema version.
pub const APPROVAL_SCHEMA_VERSION: i64 = 1;

/// Product decisions accepted by the Rust approval authority.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApprovalDecision {
    Once,
    AlwaysTool,
    AlwaysCommand,
    AlwaysTask,
    Deny,
}

impl ApprovalDecision {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "once" => Ok(Self::Once),
            "always_tool" => Ok(Self::AlwaysTool),
            "always_command" => Ok(Self::AlwaysCommand),
            "always_task" => Ok(Self::AlwaysTask),
            "deny" => Ok(Self::Deny),
            _ => Err(format!("unknown approval decision: {value}")),
        }
    }

    pub fn is_approved(self) -> bool {
        !matches!(self, Self::Deny)
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Once => "once",
            Self::AlwaysTool => "always_tool",
            Self::AlwaysCommand => "always_command",
            Self::AlwaysTask => "always_task",
            Self::Deny => "deny",
        }
    }
}

struct PendingApproval {
    tool_call_id: String,
    sender: mpsc::Sender<ApprovalDecision>,
}

/// Thread-safe approval rendezvous shared by a session's runtime worker and
/// short-lived IPC commands. At most one request is normally pending, but the
/// queue shape makes resolution deterministic if a provider proposes a batch.
pub struct ApprovalController {
    pending: Mutex<Vec<PendingApproval>>,
    tool_grants: Mutex<HashSet<String>>,
    command_grants: Mutex<HashSet<String>>,
}

impl Default for ApprovalController {
    fn default() -> Self {
        Self {
            pending: Mutex::new(Vec::new()),
            tool_grants: Mutex::new(HashSet::new()),
            command_grants: Mutex::new(HashSet::new()),
        }
    }
}

impl ApprovalController {
    pub fn begin(&self, tool_call_id: &str) -> Result<mpsc::Receiver<ApprovalDecision>, String> {
        let mut pending = self.pending.lock().unwrap();
        if pending.iter().any(|item| item.tool_call_id == tool_call_id) {
            return Err(format!("approval already pending: {tool_call_id}"));
        }
        let (sender, receiver) = mpsc::channel();
        pending.push(PendingApproval {
            tool_call_id: tool_call_id.to_string(),
            sender,
        });
        Ok(receiver)
    }

    pub fn resolve(
        &self,
        tool_call_id: Option<&str>,
        decision: ApprovalDecision,
    ) -> Result<String, String> {
        let mut pending = self.pending.lock().unwrap();
        let index = match tool_call_id {
            Some(id) => pending
                .iter()
                .position(|item| item.tool_call_id == id)
                .ok_or_else(|| format!("approval not found: {id}"))?,
            None => {
                if pending.is_empty() {
                    return Err("no approval is pending".to_string());
                }
                0
            }
        };
        let item = pending.remove(index);
        item.sender
            .send(decision)
            .map_err(|_| "approval request is no longer active".to_string())?;
        Ok(item.tool_call_id)
    }

    pub fn cancel(&self, tool_call_id: &str) -> bool {
        let mut pending = self.pending.lock().unwrap();
        if let Some(index) = pending
            .iter()
            .position(|item| item.tool_call_id == tool_call_id)
        {
            pending.remove(index);
            true
        } else {
            false
        }
    }

    pub fn pending_ids(&self) -> Vec<String> {
        self.pending
            .lock()
            .unwrap()
            .iter()
            .map(|item| item.tool_call_id.clone())
            .collect()
    }

    pub fn remember(&self, decision: ApprovalDecision, tool: &str, arguments: &Value) {
        match decision {
            ApprovalDecision::AlwaysTool | ApprovalDecision::AlwaysTask => {
                self.tool_grants.lock().unwrap().insert(tool.to_string());
            }
            ApprovalDecision::AlwaysCommand => {
                if let Some(command) = arguments.get("command").and_then(Value::as_str) {
                    self.command_grants
                        .lock()
                        .unwrap()
                        .insert(format!("{tool}\0{command}"));
                }
            }
            ApprovalDecision::Once | ApprovalDecision::Deny => {}
        }
    }

    pub fn has_standing_grant(&self, tool: &str, arguments: &Value) -> bool {
        if self.tool_grants.lock().unwrap().contains(tool) {
            return true;
        }
        arguments
            .get("command")
            .and_then(Value::as_str)
            .is_some_and(|command| {
                self.command_grants
                    .lock()
                    .unwrap()
                    .contains(&format!("{tool}\0{command}"))
            })
    }
}

/// Input for recording an approval audit event.
#[derive(Debug, Clone, Deserialize)]
pub struct ApprovalRecordInput {
    pub session_id: String,
    pub agent: Option<String>,
    pub workspace: Option<String>,
    pub connector: Option<String>,
    pub tool: String,
    pub stage: String,
    pub status: Option<String>,
    pub approval: Option<String>,
    pub arguments: Option<Value>,
    pub result_preview: Option<String>,
    pub reason: Option<String>,
    pub resource: Option<String>,
    pub level: Option<String>,
    pub isolation: Option<String>,
    pub ts: Option<f64>,
}

/// Output of recording an approval audit event.
#[derive(Debug, Clone, Serialize)]
pub struct ApprovalRecordOutput {
    pub id: i64,
    pub timestamp: String,
}

/// Approval audit writer - persists approval events to SQLite.
pub struct ApprovalWriter {
    conn: rusqlite::Connection,
}

impl ApprovalWriter {
    /// Open or create the approval audit database.
    pub fn open(path: &str) -> Result<Self, ShadowReadError> {
        let path = PathBuf::from(path);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = rusqlite::Connection::open(&path)?;
        conn.execute_batch(
            r"
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                agent TEXT,
                workspace TEXT,
                connector TEXT,
                tool TEXT,
                stage TEXT,
                status TEXT,
                approval TEXT,
                args TEXT,
                result_preview TEXT,
                reason TEXT,
                resource TEXT,
                level TEXT DEFAULT '',
                isolation TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id);
            CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_events(tool);
            CREATE INDEX IF NOT EXISTS idx_audit_stage ON audit_events(stage);
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
        ",
        )?;
        Ok(Self { conn })
    }

    /// Record an approval audit event.
    pub fn record(
        &mut self,
        input: ApprovalRecordInput,
        _ts: f64,
        workspace: &str,
    ) -> Result<ApprovalRecordOutput, ShadowReadError> {
        let timestamp = time::OffsetDateTime::now_utc()
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap_or_default();

        let args_json = input
            .arguments
            .map(|v| v.to_string())
            .unwrap_or_else(|| "{}".to_string());

        self.conn.execute(
            r"
            INSERT INTO audit_events
                (session_id, agent, workspace, connector, tool, stage, status, approval, args, result_preview, reason, resource, level, isolation, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ",
            rusqlite::params![
                input.session_id,
                input.agent.unwrap_or_default(),
                workspace,
                input.connector.unwrap_or_default(),
                input.tool,
                input.stage,
                input.status.unwrap_or_default(),
                input.approval.unwrap_or_default(),
                args_json,
                input.result_preview.unwrap_or_default(),
                input.reason.unwrap_or_default(),
                input.resource.unwrap_or_default(),
                input.level.unwrap_or_default(),
                input.isolation.unwrap_or_default(),
                timestamp,
            ],
        )?;

        let id = self.conn.last_insert_rowid();

        Ok(ApprovalRecordOutput { id, timestamp })
    }

    /// List approval audit events with optional filters.
    pub fn list(
        &self,
        limit: usize,
        session_id: Option<&str>,
        connector: Option<&str>,
        tool: Option<&str>,
    ) -> Result<Vec<Value>, ShadowReadError> {
        let mut where_clauses = Vec::new();
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

        if let Some(sid) = session_id {
            where_clauses.push("session_id = ?");
            params.push(Box::new(sid.to_string()));
        }
        if let Some(conn) = connector {
            where_clauses.push("connector = ?");
            params.push(Box::new(conn.to_string()));
        }
        if let Some(t) = tool {
            where_clauses.push("tool = ?");
            params.push(Box::new(t.to_string()));
        }

        let mut sql = "SELECT * FROM audit_events".to_string();
        if !where_clauses.is_empty() {
            sql += " WHERE ";
            sql += &where_clauses.join(" AND ");
        }
        sql += " ORDER BY id DESC LIMIT ?";
        params.push(Box::new(limit as i64));

        let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(param_refs), |row| {
            let mut item: HashMap<String, Value> = HashMap::new();
            item.insert("id".to_string(), Value::from(row.get::<_, i64>(0)?));
            item.insert(
                "timestamp".to_string(),
                Value::from(row.get::<_, String>(1)?),
            );
            item.insert(
                "session_id".to_string(),
                Value::from(row.get::<_, String>(2)?),
            );
            item.insert("agent".to_string(), Value::from(row.get::<_, String>(3)?));
            item.insert(
                "workspace".to_string(),
                Value::from(row.get::<_, String>(4)?),
            );
            item.insert(
                "connector".to_string(),
                Value::from(row.get::<_, String>(5)?),
            );
            item.insert("tool".to_string(), Value::from(row.get::<_, String>(6)?));
            item.insert("stage".to_string(), Value::from(row.get::<_, String>(7)?));
            item.insert("status".to_string(), Value::from(row.get::<_, String>(8)?));
            item.insert(
                "approval".to_string(),
                Value::from(row.get::<_, String>(9)?),
            );
            item.insert("args".to_string(), Value::from(row.get::<_, String>(10)?));
            item.insert(
                "result_preview".to_string(),
                Value::from(row.get::<_, String>(11)?),
            );
            item.insert("reason".to_string(), Value::from(row.get::<_, String>(12)?));
            item.insert(
                "resource".to_string(),
                Value::from(row.get::<_, String>(13)?),
            );
            item.insert("level".to_string(), Value::from(row.get::<_, String>(14)?));
            item.insert(
                "isolation".to_string(),
                Value::from(row.get::<_, String>(15)?),
            );
            Ok(Value::Object(item.into_iter().collect()))
        })?;

        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Close the database connection.
    pub fn close(self) -> Result<(), ShadowReadError> {
        self.conn.close().ok();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_approval_record_and_list() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("audit.db");
        let mut writer = ApprovalWriter::open(db_path.to_str().unwrap()).unwrap();

        let input = ApprovalRecordInput {
            session_id: "s1".to_string(),
            agent: Some("test-agent".to_string()),
            workspace: Some("/tmp/ws".to_string()),
            connector: Some("filesystem".to_string()),
            tool: "write_file".to_string(),
            stage: "approval_resolved".to_string(),
            status: Some("approved".to_string()),
            approval: Some("once".to_string()),
            arguments: Some(serde_json::json!({"path": "test.txt"})),
            result_preview: Some("ok".to_string()),
            reason: Some("user approved".to_string()),
            resource: Some("test.txt".to_string()),
            level: Some("L2".to_string()),
            isolation: Some("checkpoint".to_string()),
            ts: Some(1234567890.0),
        };

        let output = writer.record(input, 1234567890.0, "/tmp/ws").unwrap();
        assert!(output.id > 0);
        assert!(!output.timestamp.is_empty());

        let events = writer.list(100, Some("s1"), None, None).unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["tool"], "write_file");
        assert_eq!(events[0]["stage"], "approval_resolved");
        assert_eq!(events[0]["approval"], "once");

        writer.close().unwrap();
    }

    #[test]
    fn test_approval_schema_version_constant() {
        assert_eq!(APPROVAL_SCHEMA_VERSION, 1);
    }

    #[test]
    fn controller_resolves_the_requested_tool_call() {
        let controller = ApprovalController::default();
        let first = controller.begin("call-1").unwrap();
        let second = controller.begin("call-2").unwrap();
        assert_eq!(controller.pending_ids(), vec!["call-1", "call-2"]);
        controller
            .resolve(Some("call-2"), ApprovalDecision::Once)
            .unwrap();
        assert_eq!(second.recv().unwrap(), ApprovalDecision::Once);
        assert_eq!(controller.pending_ids(), vec!["call-1"]);
        assert!(controller.cancel("call-1"));
        assert!(first.recv().is_err());
    }

    #[test]
    fn controller_rejects_unknown_decisions_and_empty_resolution() {
        let controller = ApprovalController::default();
        assert!(ApprovalDecision::parse("approve-everything").is_err());
        assert!(controller.resolve(None, ApprovalDecision::Deny).is_err());
    }

    #[test]
    fn controller_scopes_standing_grants() {
        let controller = ApprovalController::default();
        controller.remember(
            ApprovalDecision::AlwaysCommand,
            "run_shell",
            &serde_json::json!({"command": "git status"}),
        );
        assert!(controller
            .has_standing_grant("run_shell", &serde_json::json!({"command": "git status"})));
        assert!(!controller
            .has_standing_grant("run_shell", &serde_json::json!({"command": "git push"})));
        controller.remember(
            ApprovalDecision::AlwaysTool,
            "write_file",
            &serde_json::json!({}),
        );
        assert!(controller.has_standing_grant("write_file", &serde_json::json!({})));
    }
}
