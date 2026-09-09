//! Approval Authority (R2, ADR-031).
//!
//! This module provides the sole trusted authority for approval audit recording.
//! The approval decision itself (interactive user consent) remains in Python;
//! only the audit persistence is Rust-authoritative.
//!
//! Contract: docs/architecture/adr/ADR-031-r2-approval-hard-cut.md
//! and core/audit.py (Python facade mirrors this).

use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ShadowReadError;

/// Current approval schema version.
pub const APPROVAL_SCHEMA_VERSION: i64 = 1;

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
}
