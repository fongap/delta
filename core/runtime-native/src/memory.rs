//! Rust Application Control Plane authority for durable memory and user rules.

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::{json, Value};

use crate::{durability::atomic_write, ShadowReadError};

pub struct MemoryStore {
    db_path: PathBuf,
    settings_path: PathBuf,
}

impl MemoryStore {
    pub fn open(state_dir: impl AsRef<Path>) -> Result<Self, ShadowReadError> {
        let state_dir = state_dir.as_ref();
        std::fs::create_dir_all(state_dir)?;
        let store = Self {
            db_path: state_dir.join("memory.db"),
            settings_path: state_dir.join("memory-settings.json"),
        };
        store.connection()?;
        Ok(store)
    }

    fn connection(&self) -> Result<Connection, ShadowReadError> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                key TEXT,
                content TEXT NOT NULL,
                summary TEXT,
                workspace TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );",
        )?;
        let has_summary = conn.query_row(
            "SELECT COUNT(*) FROM pragma_table_info('memories') WHERE name = 'summary'",
            [],
            |row| row.get::<_, i64>(0),
        )? > 0;
        if !has_summary {
            conn.execute("ALTER TABLE memories ADD COLUMN summary TEXT", [])?;
        }
        Ok(conn)
    }

    pub fn list(&self) -> Result<Vec<Value>, ShadowReadError> {
        let conn = self.connection()?;
        let mut stmt = conn
            .prepare("SELECT id, scope, content, summary, created_at FROM memories ORDER BY id")?;
        let rows = stmt.query_map([], |row| {
            let content: String = row.get(2)?;
            let summary: Option<String> = row.get(3)?;
            Ok(json!({
                "id": row.get::<_, i64>(0)?,
                "scope": row.get::<_, String>(1)?,
                "content": content,
                "summary": summary.filter(|value| !value.is_empty()).unwrap_or_else(|| {
                    content.lines().next().unwrap_or_default().chars().take(120).collect()
                }),
                "created_at": row.get::<_, Option<String>>(4)?.unwrap_or_default(),
            }))
        })?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(ShadowReadError::from)
    }

    pub fn add(
        &self,
        content: &str,
        scope: &str,
        workspace: Option<&str>,
        session_id: Option<&str>,
    ) -> Result<Value, ShadowReadError> {
        let conn = self.connection()?;
        conn.execute(
            "INSERT INTO memories (scope, content, workspace, session_id) VALUES (?1, ?2, ?3, ?4)",
            params![scope, content, workspace, session_id],
        )?;
        Ok(json!({"ok": true, "id": conn.last_insert_rowid()}))
    }

    pub fn update(&self, id: i64, content: &str) -> Result<Value, ShadowReadError> {
        let changed = self.connection()?.execute(
            "UPDATE memories SET content = ?1 WHERE id = ?2",
            params![content, id],
        )?;
        Ok(json!({"ok": changed > 0}))
    }

    pub fn delete(&self, id: i64) -> Result<Value, ShadowReadError> {
        let changed = self
            .connection()?
            .execute("DELETE FROM memories WHERE id = ?1", params![id])?;
        Ok(json!({"ok": changed > 0}))
    }

    pub fn delete_all(&self) -> Result<Value, ShadowReadError> {
        let deleted = self.connection()?.execute("DELETE FROM memories", [])?;
        Ok(json!({"ok": true, "deleted": deleted}))
    }

    pub fn settings(&self) -> Result<Value, ShadowReadError> {
        if !self.settings_path.exists() {
            return Ok(json!({"enabled": true, "user_rules": ""}));
        }
        let text = std::fs::read_to_string(&self.settings_path).map_err(|error| {
            ShadowReadError::Parse(format!(
                "failed to read memory settings authority {}: {error}",
                self.settings_path.display()
            ))
        })?;
        let saved = serde_json::from_str::<Value>(&text).map_err(|error| {
            ShadowReadError::Parse(format!(
                "invalid memory settings authority {}: {error}",
                self.settings_path.display()
            ))
        })?;
        let object = saved.as_object().ok_or_else(|| {
            ShadowReadError::Parse(format!(
                "memory settings authority {} must be a JSON object",
                self.settings_path.display()
            ))
        })?;
        let enabled = match object.get("enabled") {
            None => true,
            Some(Value::Bool(value)) => *value,
            Some(_) => {
                return Err(ShadowReadError::Parse(format!(
                    "memory settings authority {} has a non-boolean enabled field",
                    self.settings_path.display()
                )))
            }
        };
        let user_rules = match object.get("user_rules") {
            None => "",
            Some(Value::String(value)) => value.as_str(),
            Some(_) => {
                return Err(ShadowReadError::Parse(format!(
                    "memory settings authority {} has a non-string user_rules field",
                    self.settings_path.display()
                )))
            }
        };
        Ok(json!({
            "enabled": enabled,
            "user_rules": user_rules,
        }))
    }

    pub fn set_settings(&self, patch: &Value) -> Result<Value, ShadowReadError> {
        let mut settings = self.settings()?;
        if let Some(enabled) = patch.get("enabled").and_then(Value::as_bool) {
            settings["enabled"] = Value::Bool(enabled);
        }
        if let Some(rules) = patch.get("user_rules").and_then(Value::as_str) {
            settings["user_rules"] = Value::String(rules.chars().take(20_000).collect());
        }
        let bytes = serde_json::to_vec_pretty(&settings)?;
        atomic_write(&self.settings_path, &bytes)?;
        Ok(settings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_memory_settings_file_is_valid_default_state() {
        let temp = tempfile::tempdir().unwrap();
        let store = MemoryStore::open(temp.path()).unwrap();
        let settings = store.settings().unwrap();
        assert_eq!(settings["enabled"], true);
        assert_eq!(settings["user_rules"], "");
    }

    #[test]
    fn corrupt_memory_settings_fail_closed_and_are_not_overwritten() {
        let temp = tempfile::tempdir().unwrap();
        let store = MemoryStore::open(temp.path()).unwrap();
        std::fs::write(&store.settings_path, b"{broken").unwrap();
        let before = std::fs::read(&store.settings_path).unwrap();

        assert!(store.settings().is_err());
        assert!(store.set_settings(&json!({"enabled": false})).is_err());
        assert_eq!(std::fs::read(&store.settings_path).unwrap(), before);
    }

    #[test]
    fn invalid_memory_settings_shapes_fail_closed() {
        let temp = tempfile::tempdir().unwrap();
        let store = MemoryStore::open(temp.path()).unwrap();
        for invalid in ["[]", r#"{"enabled":"yes"}"#, r#"{"user_rules":42}"#] {
            std::fs::write(&store.settings_path, invalid).unwrap();
            assert!(store.settings().is_err(), "{invalid}");
        }
    }

    #[test]
    fn partial_memory_settings_keep_compatible_defaults() {
        let temp = tempfile::tempdir().unwrap();
        let store = MemoryStore::open(temp.path()).unwrap();
        std::fs::write(&store.settings_path, r#"{"enabled":false}"#).unwrap();
        let settings = store.settings().unwrap();
        assert_eq!(settings["enabled"], false);
        assert_eq!(settings["user_rules"], "");
    }

    #[test]
    fn memory_crud_and_settings_are_rust_owned() {
        let temp = tempfile::tempdir().unwrap();
        let store = MemoryStore::open(temp.path()).unwrap();
        let added = store.add("Remember this", "global", None, None).unwrap();
        let id = added["id"].as_i64().unwrap();
        assert_eq!(store.list().unwrap()[0]["summary"], "Remember this");
        assert_eq!(store.update(id, "Updated").unwrap()["ok"], true);
        assert_eq!(
            store
                .set_settings(&json!({"enabled": false, "user_rules": "Be concise"}))
                .unwrap()["enabled"],
            false
        );
        assert_eq!(store.delete(id).unwrap()["ok"], true);
    }
}
