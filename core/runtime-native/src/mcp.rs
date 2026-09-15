//! Rust authority for MCP server configuration.
//!
//! MCP servers are controlled workers. This store owns their product
//! configuration and never returns header/env secret values to React.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{json, Value};

use crate::ShadowReadError;

pub struct McpStore {
    path: PathBuf,
    servers: Mutex<BTreeMap<String, Value>>,
}

impl McpStore {
    pub fn open(state_dir: impl AsRef<Path>) -> Result<Self, ShadowReadError> {
        std::fs::create_dir_all(state_dir.as_ref())?;
        let path = state_dir.as_ref().join("mcp.json");
        let servers = if path.exists() {
            // MCP configuration is authoritative state. A malformed file must
            // stop authority initialization rather than silently looking like
            // an empty configuration and being overwritten on the next write.
            let value = serde_json::from_slice::<Value>(&std::fs::read(&path)?)?;
            value
                .get("mcpServers")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .collect()
        } else {
            BTreeMap::new()
        };
        Ok(Self {
            path,
            servers: Mutex::new(servers),
        })
    }

    fn save(&self, servers: &BTreeMap<String, Value>) -> Result<(), ShadowReadError> {
        std::fs::write(
            &self.path,
            serde_json::to_vec_pretty(&json!({"mcpServers": servers}))?,
        )?;
        restrict_private_file(&self.path)?;
        Ok(())
    }

    pub fn list(&self) -> Vec<Value> {
        self.servers
            .lock()
            .unwrap()
            .clone()
            .into_iter()
            .map(|(name, config)| {
                let enabled = config
                    .get("enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                let is_http = config.get("url").is_some()
                    || config
                        .get("type")
                        .and_then(Value::as_str)
                        .is_some_and(|value| {
                            matches!(
                                value,
                                "http" | "https" | "sse" | "streamable-http"
                            )
                        });
                json!({
                    "name": name,
                    "enabled": enabled,
                    "transport": if is_http { "http" } else { "stdio" },
                    "requires_approval": config.get("requires_approval").and_then(Value::as_bool).unwrap_or(true),
                    "status": if enabled { "configured" } else { "disabled" },
                    "auth": config.get("auth").cloned().unwrap_or(Value::Null),
                    "last_error": null,
                    "tool_count": null,
                    "config": redact_config(&config),
                })
            })
            .collect()
    }

    pub fn put(&self, name: &str, config: Value) -> Result<Value, ShadowReadError> {
        validate_name(name)?;
        if !config.is_object() {
            return Ok(json!({"ok": false, "error": "MCP config must be an object"}));
        }
        let mut servers = self.servers.lock().unwrap();
        let mut next = servers.clone();
        next.insert(name.to_string(), config);
        self.save(&next)?;
        *servers = next;
        Ok(json!({"ok": true}))
    }

    pub fn patch(&self, name: &str, changes: &Value) -> Result<Value, ShadowReadError> {
        validate_name(name)?;
        let mut servers = self.servers.lock().unwrap();
        let mut next = servers.clone();
        let Some(existing) = next.get_mut(name).and_then(Value::as_object_mut) else {
            return Ok(json!({"ok": false, "error": "MCP server not found"}));
        };
        if let Some(changes) = changes.as_object() {
            for (key, value) in changes {
                existing.insert(key.clone(), value.clone());
            }
        }
        self.save(&next)?;
        *servers = next;
        Ok(json!({"ok": true}))
    }

    pub fn delete(&self, name: &str) -> Result<Value, ShadowReadError> {
        validate_name(name)?;
        let mut servers = self.servers.lock().unwrap();
        let mut next = servers.clone();
        let removed = next.remove(name).is_some();
        self.save(&next)?;
        *servers = next;
        Ok(json!({"ok": removed}))
    }
}

fn validate_name(name: &str) -> Result<(), ShadowReadError> {
    if name.is_empty()
        || name.len() > 80
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(ShadowReadError::Parse(
            "invalid MCP server name".to_string(),
        ));
    }
    Ok(())
}

fn redact_config(config: &Value) -> Value {
    let mut value = config.clone();
    if let Some(object) = value.as_object_mut() {
        for field in ["env", "headers"] {
            if let Some(secrets) = object.get_mut(field).and_then(Value::as_object_mut) {
                for secret in secrets.values_mut() {
                    *secret = Value::String("••••••••".to_string());
                }
            }
        }
        for key in ["token", "api_key", "authorization"] {
            if object.contains_key(key) {
                object.insert(key.to_string(), Value::String("••••••••".to_string()));
            }
        }
    }
    value
}

#[cfg(unix)]
fn restrict_private_file(path: &Path) -> Result<(), ShadowReadError> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(windows)]
fn restrict_private_file(_path: &Path) -> Result<(), ShadowReadError> {
    // The desktop installer/first-run shell owns the app-data ACL on Windows.
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn restrict_private_file(_path: &Path) -> Result<(), ShadowReadError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_crud_redacts_secrets_from_product_response() {
        let temp = tempfile::tempdir().unwrap();
        let store = McpStore::open(temp.path()).unwrap();
        store
            .put(
                "demo",
                json!({"command": "worker", "env": {"TOKEN": "secret"}}),
            )
            .unwrap();
        let listed = store.list();
        assert_eq!(listed[0]["config"]["env"]["TOKEN"], "••••••••");
        assert!(!serde_json::to_string(&listed).unwrap().contains("secret"));
        assert_eq!(store.delete("demo").unwrap()["ok"], true);
    }

    #[test]
    fn corrupt_mcp_config_fails_closed_on_open() {
        let temp = tempfile::tempdir().unwrap();
        std::fs::write(temp.path().join("mcp.json"), b"{not-json").unwrap();
        assert!(matches!(
            McpStore::open(temp.path()),
            Err(ShadowReadError::Json(_))
        ));
    }

    #[cfg(unix)]
    #[test]
    fn persisted_mcp_config_is_private() {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir().unwrap();
        let store = McpStore::open(temp.path()).unwrap();
        store
            .put("demo", json!({"headers": {"Authorization": "secret"}}))
            .unwrap();
        let mode = std::fs::metadata(temp.path().join("mcp.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
    }
}
