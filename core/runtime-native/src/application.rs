//! Rust authority for connector configuration and product routing state.
//!
//! Credentials live in a private file and never cross the product IPC boundary.
//! Connector workers own remote protocol details; this store owns product state.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::ShadowReadError;

const CONNECTORS: &[(&str, &str, &str, bool, bool)] = &[
    ("telegram", "Telegram", "telegram", true, true),
    ("slack", "Slack", "slack", true, true),
    ("email", "Email", "email", true, false),
    ("gmail", "Gmail", "gmail", false, false),
    (
        "google_calendar",
        "Google Calendar",
        "google-calendar",
        false,
        false,
    ),
    ("browser", "Browser", "browser", false, false),
    ("github", "GitHub", "github", true, false),
    ("outlook", "Outlook", "outlook", false, false),
    ("jira", "Jira", "jira", false, false),
    ("monday", "monday.com", "monday", false, false),
    ("confluence", "Confluence", "confluence", false, false),
    ("zendesk", "Zendesk", "zendesk", false, false),
    ("linear", "Linear", "linear", false, false),
    ("gitlab", "GitLab", "gitlab", false, false),
    ("discord", "Discord", "discord", true, true),
    ("stripe", "Stripe", "stripe", false, false),
    ("asana", "Asana", "asana", false, false),
    ("hubspot", "HubSpot", "hubspot", false, false),
    ("dropbox", "Dropbox", "dropbox", false, false),
    ("box", "Box", "box", false, false),
    ("whatsapp", "WhatsApp", "whatsapp", true, true),
    ("quickbooks", "QuickBooks", "quickbooks", false, false),
    ("datadog", "Datadog", "datadog", false, false),
    ("salesforce", "Salesforce", "salesforce", false, false),
    ("docusign", "DocuSign", "docusign", false, false),
    ("clickup", "ClickUp", "clickup", false, false),
    ("google_drive", "Google Drive", "google-drive", false, false),
    ("canva", "Canva", "canva", false, false),
    ("figma", "Figma", "figma", false, false),
    ("descript", "Descript", "descript", false, false),
    ("clay", "Clay", "clay", false, false),
    ("close", "Close", "close", false, false),
    ("notion", "Notion", "notion", false, false),
    ("attio", "Attio", "attio", false, false),
    ("posthog", "PostHog", "posthog", false, false),
    ("mixpanel", "Mixpanel", "mixpanel", false, false),
    ("amplitude", "Amplitude", "amplitude", false, false),
    ("apollo", "Apollo", "apollo", false, false),
    ("hunter", "Hunter", "hunter", false, false),
    ("pagerduty", "PagerDuty", "pagerduty", false, false),
];

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ConnectorState {
    connected: bool,
    enabled: bool,
    account: Option<String>,
    #[serde(default)]
    tools: BTreeMap<String, bool>,
    #[serde(default)]
    details: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ApplicationState {
    #[serde(default)]
    connectors: BTreeMap<String, ConnectorState>,
    #[serde(default)]
    session_connections: BTreeMap<String, BTreeMap<String, bool>>,
    #[serde(default)]
    subscriptions: Vec<Value>,
    #[serde(default)]
    inbox_bindings: Vec<Value>,
    #[serde(default)]
    unrouted: Vec<Value>,
    #[serde(default)]
    recent_channels: Vec<Value>,
    dm_session: Option<String>,
}

pub struct ApplicationStore {
    state_path: PathBuf,
    secrets_path: PathBuf,
}

impl ApplicationStore {
    pub fn open(state_dir: impl AsRef<Path>) -> Result<Self, ShadowReadError> {
        std::fs::create_dir_all(state_dir.as_ref())?;
        Ok(Self {
            state_path: state_dir.as_ref().join("application-state.json"),
            secrets_path: state_dir.as_ref().join("connector-secrets.json"),
        })
    }

    fn read(&self) -> ApplicationState {
        std::fs::read_to_string(&self.state_path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    fn write(&self, state: &ApplicationState) -> Result<(), ShadowReadError> {
        std::fs::write(&self.state_path, serde_json::to_vec_pretty(state)?)?;
        Ok(())
    }

    pub fn connectors(&self) -> Vec<Value> {
        let state = self.read();
        CONNECTORS
            .iter()
            .map(|(name, title, logo, two_way, channels)| {
                let current = state.connectors.get(*name).cloned().unwrap_or_default();
                let tools = current
                    .tools
                    .iter()
                    .map(|(tool, enabled)| {
                        json!({
                            "name": tool, "label": tool, "kind": "write",
                            "description": "Connector worker capability", "enabled": enabled,
                            "requires_approval": true,
                        })
                    })
                    .collect::<Vec<_>>();
                let mut row = json!({
                    "name": name, "title": title, "icon": logo, "logo": logo,
                    "blurb": format!("Connect Delta to {title}."), "about": "", "access": [],
                    "auth": "token", "two_way": two_way, "channels": channels,
                    "available": true, "fields": [], "instructions": [],
                    "connected": current.connected, "account": current.account,
                    "enabled": current.enabled, "brand_color": "#6b7280",
                    "allowed_users": [], "tools": tools, "managed": false,
                    "managed_profile": false,
                });
                if let Some(object) = row.as_object_mut() {
                    for (key, value) in current.details {
                        object.insert(key, value);
                    }
                }
                row
            })
            .collect()
    }

    pub fn connect(
        &self,
        name: &str,
        fields: &BTreeMap<String, String>,
    ) -> Result<Value, ShadowReadError> {
        if !CONNECTORS.iter().any(|descriptor| descriptor.0 == name) {
            return Ok(json!({"ok": false, "error": "unknown connector"}));
        }
        if fields.is_empty() && name != "browser" {
            return Ok(json!({"ok": false, "error": "connector credentials are required"}));
        }
        let mut secrets = std::fs::read_to_string(&self.secrets_path)
            .ok()
            .and_then(|text| {
                serde_json::from_str::<BTreeMap<String, BTreeMap<String, String>>>(&text).ok()
            })
            .unwrap_or_default();
        secrets.insert(name.to_string(), fields.clone());
        std::fs::write(&self.secrets_path, serde_json::to_vec_pretty(&secrets)?)?;
        let account = fields
            .get("email")
            .or_else(|| fields.get("account"))
            .or_else(|| fields.get("workspace"))
            .cloned()
            .unwrap_or_else(|| "Connected".to_string());
        let mut state = self.read();
        let previous = state.connectors.remove(name).unwrap_or_default();
        state.connectors.insert(
            name.to_string(),
            ConnectorState {
                connected: true,
                enabled: true,
                account: Some(account.clone()),
                tools: previous.tools,
                details: previous.details,
            },
        );
        self.write(&state)?;
        Ok(json!({"ok": true, "account": account}))
    }

    pub fn disconnect(&self, name: &str) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        let removed = state.connectors.remove(name).is_some();
        self.write(&state)?;
        let mut secrets = std::fs::read_to_string(&self.secrets_path)
            .ok()
            .and_then(|text| serde_json::from_str::<BTreeMap<String, Value>>(&text).ok())
            .unwrap_or_default();
        secrets.remove(name);
        std::fs::write(&self.secrets_path, serde_json::to_vec_pretty(&secrets)?)?;
        Ok(json!({"ok": removed}))
    }

    pub fn update_tools(
        &self,
        name: &str,
        enabled: &BTreeMap<String, bool>,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        let connector = state.connectors.entry(name.to_string()).or_default();
        for (tool, value) in enabled {
            connector.tools.insert(tool.clone(), *value);
        }
        let tools = connector.tools.clone();
        self.write(&state)?;
        Ok(json!({"ok": true, "tools": tools}))
    }

    pub fn action(
        &self,
        name: &str,
        action: &str,
        payload: &Value,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        let connector = state.connectors.entry(name.to_string()).or_default();
        let array_add = |details: &mut BTreeMap<String, Value>, key: &str, value: String| {
            let values = details.entry(key.to_string()).or_insert_with(|| json!([]));
            if let Some(values) = values.as_array_mut() {
                if !values.iter().any(|item| item.as_str() == Some(&value)) {
                    values.push(Value::String(value));
                }
            }
        };
        let array_remove = |details: &mut BTreeMap<String, Value>, key: &str, value: &str| {
            if let Some(values) = details.get_mut(key).and_then(Value::as_array_mut) {
                values.retain(|item| item.as_str() != Some(value));
            }
        };
        let response = match action {
            "allow_user" => {
                array_add(
                    &mut connector.details,
                    "allowed_users",
                    payload
                        .get("user_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                );
                json!({"ok": true})
            }
            "disallow_user" => {
                array_remove(
                    &mut connector.details,
                    "allowed_users",
                    payload
                        .get("user_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                );
                json!({"ok": true})
            }
            "add_approval_owner" => {
                array_add(
                    &mut connector.details,
                    "approval_owner_ids",
                    payload
                        .get("user_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                );
                json!({"ok": true})
            }
            "remove_approval_owner" => {
                array_remove(
                    &mut connector.details,
                    "approval_owner_ids",
                    payload
                        .get("user_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                );
                json!({"ok": true})
            }
            "set_filters" => {
                connector
                    .details
                    .insert("filters".to_string(), payload.clone());
                json!({"ok": true, "filters": payload})
            }
            "set_hidden_fields" => {
                let fields = payload.get("fields").cloned().unwrap_or_else(|| json!([]));
                connector
                    .details
                    .insert("hidden_fields".to_string(), fields.clone());
                json!({"ok": true, "hidden_fields": fields})
            }
            "directory" => json!({"ok": true, "members": []}),
            "channels" => json!({"ok": true, "channels": []}),
            "github_status" => {
                json!({"ok": true, "mode": "", "relay": {"state": "offline", "reconnects": 0, "last_event_at": null, "last_error": ""}, "installs": {}, "missed": {}})
            }
            "slack_status" => {
                json!({"mode": "", "relay": {"state": "offline", "reconnects": 0, "last_event_at": null, "last_error": ""}, "teams": {}})
            }
            "resolve_unauthorized" => json!({"ok": true}),
            "disconnect_account"
            | "set_default_account"
            | "disconnect_workspace"
            | "disconnect_installation"
            | "disconnect_portal"
            | "set_default_portal" => {
                json!({"ok": true, "remaining_accounts": 0, "remaining_workspaces": 0, "remaining_installs": 0, "remaining_portals": 0})
            }
            _ => json!({"ok": false, "error": format!("unsupported connector action: {action}")}),
        };
        self.write(&state)?;
        Ok(response)
    }

    pub fn session_connections(&self, session_id: &str) -> Value {
        let state = self.read();
        let overrides = state.session_connections.get(session_id);
        let connected = state.connectors.iter().filter(|(_, connector)| connector.connected)
            .map(|(name, connector)| json!({
                "connector": name,
                "enabled": overrides.and_then(|values| values.get(name)).copied().unwrap_or(connector.enabled),
                "detail": connector.account.clone().unwrap_or_default(),
            })).collect::<Vec<_>>();
        json!({"connected": connected, "recommended": [], "attention": 0})
    }

    pub fn set_session_connection(
        &self,
        session_id: &str,
        connector: &str,
        enabled: bool,
        clear: bool,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        let overrides = state
            .session_connections
            .entry(session_id.to_string())
            .or_default();
        if clear {
            overrides.remove(connector);
        } else {
            overrides.insert(connector.to_string(), enabled);
        }
        self.write(&state)?;
        Ok(json!({"ok": true}))
    }

    pub fn subscriptions(&self) -> Vec<Value> {
        self.read().subscriptions
    }

    pub fn subscribe(&self, session_id: &str, channel: &str) -> Result<Value, ShadowReadError> {
        if session_id.is_empty() || channel.trim().is_empty() {
            return Ok(json!({"ok": false, "error": "session and channel are required"}));
        }
        let mut state = self.read();
        state.subscriptions.retain(|item| {
            item.get("session_id").and_then(Value::as_str) != Some(session_id)
                || item.get("channel").and_then(Value::as_str) != Some(channel)
        });
        state.subscriptions.push(
            json!({"session_id": session_id, "session_title": "", "agent": "delta",
            "channel": channel, "channel_name": null, "routing_target": null, "collision": false}),
        );
        self.write(&state)?;
        Ok(json!({"ok": true, "channel": channel}))
    }

    pub fn unsubscribe(&self, session_id: &str, channel: &str) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        let before = state.subscriptions.len();
        state.subscriptions.retain(|item| {
            item.get("session_id").and_then(Value::as_str) != Some(session_id)
                || item.get("channel").and_then(Value::as_str) != Some(channel)
        });
        let removed = state.subscriptions.len() != before;
        self.write(&state)?;
        Ok(json!({"ok": true, "removed": removed}))
    }

    pub fn inbox_bindings(&self) -> Vec<Value> {
        self.read().inbox_bindings
    }

    pub fn set_inbox_binding(
        &self,
        name: &str,
        channel: Option<&str>,
        target: &str,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        state
            .inbox_bindings
            .retain(|item| item.get("name").and_then(Value::as_str) != Some(name));
        state
            .inbox_bindings
            .push(json!({"name": name, "channel": channel, "target": target}));
        let bindings = state.inbox_bindings.clone();
        self.write(&state)?;
        Ok(json!({"ok": true, "bindings": bindings}))
    }

    pub fn unrouted(&self) -> Vec<Value> {
        self.read().unrouted
    }
    pub fn recent_channels(&self) -> Vec<Value> {
        self.read().recent_channels
    }
    pub fn dm_route(&self) -> Option<String> {
        self.read().dm_session
    }

    pub fn set_dm_route(&self, session_id: &str) -> Result<Value, ShadowReadError> {
        let mut state = self.read();
        state.dm_session = (!session_id.is_empty()).then(|| session_id.to_string());
        let route = state.dm_session.clone();
        self.write(&state)?;
        Ok(json!({"ok": true, "dm_session": route}))
    }

    pub fn connected_names(&self) -> BTreeSet<String> {
        self.read()
            .connectors
            .into_iter()
            .filter(|(_, state)| state.connected && state.enabled)
            .map(|(name, _)| name)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connector_secrets_never_cross_the_product_boundary() {
        let temp = tempfile::tempdir().unwrap();
        let store = ApplicationStore::open(temp.path()).unwrap();
        store
            .connect(
                "slack",
                &BTreeMap::from([("token".to_string(), "secret-value".to_string())]),
            )
            .unwrap();
        assert!(!serde_json::to_string(&store.connectors())
            .unwrap()
            .contains("secret-value"));
        assert!(store.connected_names().contains("slack"));
    }

    #[test]
    fn subscriptions_are_idempotent_and_removable() {
        let temp = tempfile::tempdir().unwrap();
        let store = ApplicationStore::open(temp.path()).unwrap();
        store.subscribe("s1", "slack:C1").unwrap();
        store.subscribe("s1", "slack:C1").unwrap();
        assert_eq!(store.subscriptions().len(), 1);
        assert_eq!(
            store.unsubscribe("s1", "slack:C1").unwrap()["removed"],
            true
        );
    }
}
