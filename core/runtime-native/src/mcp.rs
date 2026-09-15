//! Rust authority for MCP server configuration.
//!
//! MCP servers are controlled workers. This store owns their product
//! configuration and never returns persisted secret values to React.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{json, Map, Value};

use crate::{durability::atomic_write_private, ShadowReadError};

const REDACTED: &str = "••••••••";

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
            parse_persisted_servers(&value)?
        } else {
            BTreeMap::new()
        };
        Ok(Self {
            path,
            servers: Mutex::new(servers),
        })
    }

    fn save(&self, servers: &BTreeMap<String, Value>) -> Result<(), ShadowReadError> {
        let bytes = serde_json::to_vec_pretty(&json!({"mcpServers": servers}))?;
        atomic_write_private(&self.path, &bytes)?;
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
                let public_auth = config
                    .get("auth")
                    .and_then(Value::as_str)
                    .filter(|value| *value == "oauth")
                    .map(|_| Value::String("oauth".to_string()))
                    .unwrap_or(Value::Null);
                json!({
                    "name": name,
                    "enabled": enabled,
                    "transport": if is_http { "http" } else { "stdio" },
                    "requires_approval": config.get("requires_approval").and_then(Value::as_bool).unwrap_or(true),
                    "status": if enabled { "configured" } else { "disabled" },
                    // Product surfaces only need the OAuth mode marker. Persisted
                    // auth objects/tokens are never part of the React contract.
                    "auth": public_auth,
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
        let Some(changes) = changes.as_object() else {
            return Ok(json!({"ok": false, "error": "MCP changes must be an object"}));
        };
        let mut servers = self.servers.lock().unwrap();
        let mut next = servers.clone();
        let Some(existing) = next.get_mut(name).and_then(Value::as_object_mut) else {
            return Ok(json!({"ok": false, "error": "MCP server not found"}));
        };
        for (key, value) in changes {
            existing.insert(key.clone(), value.clone());
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

fn parse_persisted_servers(value: &Value) -> Result<BTreeMap<String, Value>, ShadowReadError> {
    let root = value
        .as_object()
        .ok_or_else(|| ShadowReadError::Parse("MCP state root must be an object".to_string()))?;
    let servers = root
        .get("mcpServers")
        .ok_or_else(|| ShadowReadError::Parse("MCP state is missing mcpServers".to_string()))?
        .as_object()
        .ok_or_else(|| ShadowReadError::Parse("MCP mcpServers must be an object".to_string()))?;

    let mut parsed = BTreeMap::new();
    for (name, config) in servers {
        validate_name(name)?;
        validate_server_config(config)?;
        parsed.insert(name.clone(), config.clone());
    }
    Ok(parsed)
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

fn validate_server_config(config: &Value) -> Result<(), ShadowReadError> {
    if !config.is_object() {
        return Err(ShadowReadError::Parse(
            "MCP server config must be an object".to_string(),
        ));
    }
    Ok(())
}

fn redact_config(config: &Value) -> Value {
    redact_value(config, None)
}

fn redact_value(value: &Value, parent_key: Option<&str>) -> Value {
    match value {
        Value::Object(object) => {
            let redact_all_children = parent_key.is_some_and(is_secret_container_key);
            let mut redacted = Map::new();
            for (key, child) in object {
                let next = if redact_all_children
                    || is_secret_key(key)
                    || (key.eq_ignore_ascii_case("auth") && child.as_str() != Some("oauth"))
                {
                    Value::String(REDACTED.to_string())
                } else {
                    redact_value(child, Some(key))
                };
                redacted.insert(key.clone(), next);
            }
            Value::Object(redacted)
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| redact_value(item, parent_key))
                .collect(),
        ),
        _ if parent_key.is_some_and(is_secret_container_key) => Value::String(REDACTED.to_string()),
        _ => value.clone(),
    }
}

fn is_secret_container_key(key: &str) -> bool {
    key.eq_ignore_ascii_case("env") || key.eq_ignore_ascii_case("headers")
}

fn is_secret_key(key: &str) -> bool {
    matches!(
        key.to_ascii_lowercase().as_str(),
        "token"
            | "access_token"
            | "refresh_token"
            | "api_key"
            | "apikey"
            | "api-key"
            | "authorization"
            | "password"
            | "secret"
            | "client_secret"
            | "credential"
            | "credentials"
    )
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
        assert_eq!(listed[0]["config"]["env"]["TOKEN"], REDACTED);
        assert!(!serde_json::to_string(&listed).unwrap().contains("secret"));
        assert_eq!(store.delete("demo").unwrap()["ok"], true);
    }

    #[test]
    fn list_exposes_only_oauth_metadata_and_redacts_auth_payload() {
        let temp = tempfile::tempdir().unwrap();
        let store = McpStore::open(temp.path()).unwrap();
        store
            .put(
                "sensitive",
                json!({
                    "url": "https://example.test/mcp",
                    "auth": {"type": "bearer", "access_token": "deep-token"},
                    "headers": {"Authorization": "header-secret"},
                    "nested": {"client_secret": "client-secret", "safe": "visible"}
                }),
            )
            .unwrap();

        let listed = store.list();
        assert!(listed[0]["auth"].is_null());
        assert_eq!(listed[0]["config"]["auth"], REDACTED);
        assert_eq!(listed[0]["config"]["headers"]["Authorization"], REDACTED);
        assert_eq!(listed[0]["config"]["nested"]["client_secret"], REDACTED);
        assert_eq!(listed[0]["config"]["nested"]["safe"], "visible");
        let serialized = serde_json::to_string(&listed).unwrap();
        for secret in ["deep-token", "header-secret", "client-secret"] {
            assert!(!serialized.contains(secret));
        }
    }

    #[test]
    fn oauth_auth_mode_is_exposed_without_credentials() {
        let temp = tempfile::tempdir().unwrap();
        let store = McpStore::open(temp.path()).unwrap();
        store
            .put(
                "oauth-demo",
                json!({"url": "https://example.test/mcp", "auth": "oauth"}),
            )
            .unwrap();
        let listed = store.list();
        assert_eq!(listed[0]["auth"], "oauth");
        assert_eq!(listed[0]["config"]["auth"], "oauth");
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

    #[test]
    fn valid_json_with_invalid_mcp_shape_fails_closed_on_open() {
        for payload in [
            json!({}),
            json!({"mcpServers": []}),
            json!({"mcpServers": {"demo": "not-an-object"}}),
        ] {
            let temp = tempfile::tempdir().unwrap();
            std::fs::write(
                temp.path().join("mcp.json"),
                serde_json::to_vec(&payload).unwrap(),
            )
            .unwrap();
            assert!(matches!(
                McpStore::open(temp.path()),
                Err(ShadowReadError::Parse(_))
            ));
        }
    }

    #[test]
    fn patch_rejects_non_object_changes() {
        let temp = tempfile::tempdir().unwrap();
        let store = McpStore::open(temp.path()).unwrap();
        store.put("demo", json!({"command": "worker"})).unwrap();
        let result = store
            .patch("demo", &json!(["not", "an", "object"]))
            .unwrap();
        assert_eq!(result["ok"], false);
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
