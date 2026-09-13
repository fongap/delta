//! Rust Application Control Plane authority for durable human-attention items.
//!
//! Approval workers may park an item here, but only the Runtime/IPC authority
//! can create or resolve it. Persistence uses the legacy-compatible
//! `inbox.json` shape so existing user data is migrated in place.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::ShadowReadError;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InboxItem {
    pub id: String,
    pub session_id: String,
    pub kind: String,
    pub title: String,
    #[serde(default)]
    pub body: String,
    pub state: String,
    #[serde(default)]
    pub resolution: Option<String>,
    #[serde(default = "default_inbox")]
    pub inbox: String,
    pub created_at: String,
    #[serde(default)]
    pub resolved_at: Option<String>,
    #[serde(default = "default_visibility")]
    pub visibility: String,
    #[serde(default)]
    pub tool_call_id: Option<String>,
    #[serde(default)]
    pub options: Vec<Value>,
    #[serde(default = "default_true")]
    pub allow_text: bool,
    #[serde(default)]
    pub multi: bool,
    #[serde(default)]
    pub header: String,
    #[serde(default)]
    pub questions: Vec<Value>,
    #[serde(default)]
    pub data: Value,
}

fn default_inbox() -> String {
    "default".to_string()
}

fn default_visibility() -> String {
    "inbox".to_string()
}

fn default_true() -> bool {
    true
}

#[derive(Default, Serialize, Deserialize)]
struct InboxFile {
    #[serde(default)]
    items: Vec<InboxItem>,
}

pub struct InboxStore {
    path: PathBuf,
    items: Mutex<Vec<InboxItem>>,
}

impl InboxStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, ShadowReadError> {
        let path = path.as_ref().to_path_buf();
        let items = if path.exists() {
            serde_json::from_slice::<InboxFile>(&std::fs::read(&path)?)
                .unwrap_or_default()
                .items
        } else {
            Vec::new()
        };
        Ok(Self {
            path,
            items: Mutex::new(items),
        })
    }

    fn save(&self, items: &[InboxItem]) -> Result<(), ShadowReadError> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(
            &self.path,
            serde_json::to_vec_pretty(&InboxFile {
                items: items.to_vec(),
            })?,
        )?;
        Ok(())
    }

    pub fn add_approval(
        &self,
        session_id: &str,
        tool_call_id: &str,
        tool: &str,
        arguments: &Value,
        reason: &str,
    ) -> Result<InboxItem, ShadowReadError> {
        let mut items = self.items.lock().unwrap();
        if let Some(existing) = items.iter().find(|item| {
            item.session_id == session_id && item.tool_call_id.as_deref() == Some(tool_call_id)
        }) {
            return Ok(existing.clone());
        }
        let now = time::OffsetDateTime::now_utc()
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap_or_default();
        let item = InboxItem {
            id: uuid::Uuid::new_v4().simple().to_string(),
            session_id: session_id.to_string(),
            kind: "approval".to_string(),
            title: format!("Run {tool}?"),
            body: reason.to_string(),
            state: "pending".to_string(),
            resolution: None,
            inbox: default_inbox(),
            created_at: now,
            resolved_at: None,
            visibility: default_visibility(),
            tool_call_id: Some(tool_call_id.to_string()),
            options: Vec::new(),
            allow_text: true,
            multi: false,
            header: String::new(),
            questions: Vec::new(),
            data: json!({"tool": tool, "arguments": arguments}),
        };
        items.push(item.clone());
        self.save(&items)?;
        Ok(item)
    }

    pub fn add_interaction(
        &self,
        session_id: &str,
        tool_call_id: &str,
        kind: &str,
        arguments: &Value,
    ) -> Result<InboxItem, ShadowReadError> {
        let mut items = self.items.lock().unwrap();
        if let Some(existing) = items.iter().find(|item| {
            item.session_id == session_id && item.tool_call_id.as_deref() == Some(tool_call_id)
        }) {
            return Ok(existing.clone());
        }
        let now = time::OffsetDateTime::now_utc()
            .format(&time::format_description::well_known::Rfc3339)
            .unwrap_or_default();
        let title = match kind {
            "question" => arguments
                .get("question")
                .and_then(Value::as_str)
                .unwrap_or("Question"),
            "directory" => "Directory access requested",
            "plan" => "Plan approval requested",
            _ => "User input requested",
        };
        let item = InboxItem {
            id: uuid::Uuid::new_v4().simple().to_string(),
            session_id: session_id.to_string(),
            kind: kind.to_string(),
            title: title.to_string(),
            body: arguments
                .get("reason")
                .or_else(|| arguments.get("plan"))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            state: "pending".to_string(),
            resolution: None,
            inbox: default_inbox(),
            created_at: now,
            resolved_at: None,
            visibility: default_visibility(),
            tool_call_id: Some(tool_call_id.to_string()),
            options: arguments
                .get("options")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default(),
            allow_text: arguments
                .get("allow_text")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            multi: arguments
                .get("multi")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            header: arguments
                .get("header")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            questions: arguments
                .get("questions")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default(),
            data: arguments.clone(),
        };
        items.push(item.clone());
        self.save(&items)?;
        Ok(item)
    }

    pub fn get(&self, item_id: &str) -> Option<InboxItem> {
        self.items
            .lock()
            .unwrap()
            .iter()
            .find(|item| item.id == item_id)
            .cloned()
    }

    pub fn list(&self, session_id: Option<&str>, state: Option<&str>) -> Vec<InboxItem> {
        self.items
            .lock()
            .unwrap()
            .iter()
            .filter(|item| item.visibility == "inbox")
            .filter(|item| session_id.is_none_or(|value| item.session_id == value))
            .filter(|item| state.is_none_or(|value| item.state == value))
            .cloned()
            .collect()
    }

    pub fn resolve(
        &self,
        item_id: &str,
        resolution: &str,
    ) -> Result<Option<InboxItem>, ShadowReadError> {
        let mut items = self.items.lock().unwrap();
        let Some(position) = items.iter().position(|item| item.id == item_id) else {
            return Ok(None);
        };
        if items[position].state == "pending" {
            items[position].state = "resolved".to_string();
            items[position].resolution = Some(resolution.to_string());
            items[position].resolved_at = Some(
                time::OffsetDateTime::now_utc()
                    .format(&time::format_description::well_known::Rfc3339)
                    .unwrap_or_default(),
            );
            self.save(&items)?;
        }
        Ok(Some(items[position].clone()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn approval_is_idempotent_and_first_resolution_wins() {
        let temp = tempfile::tempdir().unwrap();
        let store = InboxStore::open(temp.path().join("inbox.json")).unwrap();
        let first = store
            .add_approval("s1", "call-1", "write_file", &json!({"path": "a"}), "write")
            .unwrap();
        let again = store
            .add_approval("s1", "call-1", "write_file", &json!({}), "write")
            .unwrap();
        assert_eq!(first.id, again.id);
        store.resolve(&first.id, "allow").unwrap();
        let resolved = store.resolve(&first.id, "deny").unwrap().unwrap();
        assert_eq!(resolved.resolution.as_deref(), Some("allow"));
    }
}
