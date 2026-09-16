//! Rust authority for connector configuration and product routing state.
//!
//! Product state and connector credentials live in one private authority file.
//! Secrets never cross the product IPC boundary; connector workers own remote protocol details.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{durability::atomic_write_private, ShadowReadError};

/// The single Connector Catalog Authority (R7 Task 4). Each entry carries the
/// full business metadata for a connector: id, title, logo, auth requirements,
/// two-way / channels characteristics, blurb and brand color. The frontend and
/// every other layer read this catalog via [`ApplicationStore::connectors`]; no
/// other source may maintain a business connector list. Connector *secrets*
/// remain in this authority's `secrets` map; connector *worker* protocol
/// bindings live in the worker package, not here.
const CONNECTORS: &[ConnectorSpec] = &[
    ConnectorSpec { name: "telegram", title: "Telegram", logo: "telegram", auth: "bot_token", two_way: true, channels: true, blurb: "Two-way messaging with a Telegram bot.", brand_color: "#229ed9" },
    ConnectorSpec { name: "slack", title: "Slack", logo: "slack", auth: "socket_app", two_way: true, channels: true, blurb: "Two-way messaging via a Slack app using Socket Mode.", brand_color: "#611f69" },
    ConnectorSpec { name: "email", title: "Email (IMAP)", logo: "email", auth: "app_password", two_way: false, channels: false, blurb: "Read, search, and send mail from any IMAP account — Gmail, iCloud, Fastmail, or custom.", brand_color: "#6b7280" },
    ConnectorSpec { name: "gmail", title: "Gmail", logo: "gmail", auth: "oauth", two_way: false, channels: false, blurb: "Search, summarize, draft, and send email.", brand_color: "#ea4335" },
    ConnectorSpec { name: "google_calendar", title: "Google Calendar", logo: "google-calendar", auth: "oauth", two_way: false, channels: false, blurb: "Read availability, summarize schedules, and create events.", brand_color: "#4285f4" },
    ConnectorSpec { name: "browser", title: "Browser", logo: "browser", auth: "none", two_way: false, channels: false, blurb: "Let agents navigate, read, and act on websites with approval.", brand_color: "#0ea5e9" },
    ConnectorSpec { name: "github", title: "GitHub", logo: "github", auth: "token", two_way: true, channels: false, blurb: "Work with issues, pull requests, repository files, and CI status.", brand_color: "#1f2328" },
    ConnectorSpec { name: "outlook", title: "Outlook", logo: "outlook", auth: "oauth", two_way: false, channels: false, blurb: "Microsoft 365 mail and calendar: search, draft, and send email; manage events and respond to invites.", brand_color: "#0078d4" },
    ConnectorSpec { name: "jira", title: "Jira", logo: "jira", auth: "api_token", two_way: false, channels: false, blurb: "Search, summarize, create, and update issues.", brand_color: "#0052cc" },
    ConnectorSpec { name: "monday", title: "monday.com", logo: "monday", auth: "oauth", two_way: false, channels: false, blurb: "Read boards and items, track work, create items and post updates.", brand_color: "#6161ff" },
    ConnectorSpec { name: "confluence", title: "Confluence", logo: "confluence", auth: "api_token", two_way: false, channels: false, blurb: "Search spaces, read pages, and draft documentation.", brand_color: "#172b4d" },
    ConnectorSpec { name: "zendesk", title: "Zendesk", logo: "zendesk", auth: "api_token", two_way: false, channels: false, blurb: "Search tickets, summarize customer context, and draft replies.", brand_color: "#03363d" },
    ConnectorSpec { name: "linear", title: "Linear", logo: "linear", auth: "api_token", two_way: false, channels: false, blurb: "Search, read, and create Linear issues.", brand_color: "#5e6ad2" },
    ConnectorSpec { name: "gitlab", title: "GitLab", logo: "gitlab", auth: "token", two_way: false, channels: false, blurb: "Work with issues and merge requests on GitLab.com or self-hosted.", brand_color: "#fc6d26" },
    ConnectorSpec { name: "discord", title: "Discord", logo: "discord", auth: "bot_token", two_way: false, channels: true, blurb: "Read channels and send messages through a Discord bot.", brand_color: "#5865f2" },
    ConnectorSpec { name: "stripe", title: "Stripe", logo: "stripe", auth: "api_token", two_way: false, channels: false, blurb: "Read-only access to customers, charges, and invoices.", brand_color: "#635bff" },
    ConnectorSpec { name: "asana", title: "Asana", logo: "asana", auth: "token", two_way: false, channels: false, blurb: "Search and read tasks and projects; create, update, and comment.", brand_color: "#f06a6a" },
    ConnectorSpec { name: "hubspot", title: "HubSpot", logo: "hubspot", auth: "token", two_way: false, channels: false, blurb: "Search CRM records; log notes and tasks, update records. No deletes.", brand_color: "#ff7a59" },
    ConnectorSpec { name: "dropbox", title: "Dropbox", logo: "dropbox", auth: "oauth", two_way: false, channels: false, blurb: "Search, browse, and read files in Dropbox.", brand_color: "#0061ff" },
    ConnectorSpec { name: "box", title: "Box", logo: "box", auth: "oauth", two_way: false, channels: false, blurb: "Search, browse, and read files in Box.", brand_color: "#0061d5" },
    ConnectorSpec { name: "whatsapp", title: "WhatsApp", logo: "whatsapp", auth: "token", two_way: false, channels: true, blurb: "Send WhatsApp messages through Meta's official Cloud API (outbound only).", brand_color: "#25d366" },
    ConnectorSpec { name: "quickbooks", title: "QuickBooks", logo: "quickbooks", auth: "oauth", two_way: false, channels: false, blurb: "Read-only access to customers, invoices, and financial reports.", brand_color: "#2ca01c" },
    ConnectorSpec { name: "datadog", title: "Datadog", logo: "datadog", auth: "none", two_way: false, channels: false, blurb: "Pull firing alerts, monitors, and the incident timeline.", brand_color: "#632ca6" },
    ConnectorSpec { name: "salesforce", title: "Salesforce", logo: "salesforce", auth: "none", two_way: false, channels: false, blurb: "Read and update cases, accounts, and opportunities in the CRM.", brand_color: "#00a1e0" },
    ConnectorSpec { name: "docusign", title: "Docusign", logo: "docusign", auth: "oauth", two_way: false, channels: false, blurb: "Track agreements, check envelope status, and send documents for signature.", brand_color: "#4c00ff" },
    ConnectorSpec { name: "clickup", title: "ClickUp", logo: "clickup", auth: "api_token", two_way: false, channels: false, blurb: "Search tasks and docs; create and update items.", brand_color: "#7b68ee" },
    ConnectorSpec { name: "google_drive", title: "Google Drive", logo: "google-drive", auth: "oauth", two_way: false, channels: false, blurb: "Search, browse, and read files in Google Drive.", brand_color: "#4285f4" },
    ConnectorSpec { name: "canva", title: "Canva", logo: "canva", auth: "oauth", two_way: false, channels: false, blurb: "Browse, create, and export designs.", brand_color: "#00c4cc" },
    ConnectorSpec { name: "figma", title: "Figma", logo: "figma", auth: "api_token", two_way: false, channels: false, blurb: "Read design files and comments; export assets.", brand_color: "#f24e1e" },
    ConnectorSpec { name: "descript", title: "Descript", logo: "descript", auth: "none", two_way: false, channels: false, blurb: "Read and edit audio and video projects through their transcripts.", brand_color: "#0062ff" },
    ConnectorSpec { name: "clay", title: "Clay", logo: "clay", auth: "none", two_way: false, channels: false, blurb: "Enrich people and companies; run outbound research workflows.", brand_color: "#1f2328" },
    ConnectorSpec { name: "close", title: "Close", logo: "close", auth: "api_token", two_way: false, channels: false, blurb: "Read and update leads, contacts, and opportunities in the CRM.", brand_color: "#276392" },
    ConnectorSpec { name: "notion", title: "Notion", logo: "notion", auth: "oauth", two_way: false, channels: false, blurb: "Search pages, read content, query databases, create pages.", brand_color: "#1f2328" },
    ConnectorSpec { name: "attio", title: "Attio", logo: "attio", auth: "oauth", two_way: false, channels: false, blurb: "Read your Attio CRM: objects, records, notes.", brand_color: "#2d7ff9" },
    ConnectorSpec { name: "posthog", title: "PostHog", logo: "posthog", auth: "api_token", two_way: false, channels: false, blurb: "Query product analytics: events, funnels, saved insights.", brand_color: "#f54e00" },
    ConnectorSpec { name: "mixpanel", title: "Mixpanel", logo: "mixpanel", auth: "api_token", two_way: false, channels: false, blurb: "Query Mixpanel events and segmentation.", brand_color: "#7856ff" },
    ConnectorSpec { name: "amplitude", title: "Amplitude", logo: "amplitude", auth: "api_token", two_way: false, channels: false, blurb: "Query Amplitude charts data: active users, event totals.", brand_color: "#1e61f0" },
    ConnectorSpec { name: "apollo", title: "Apollo.io", logo: "apollo", auth: "api_token", two_way: false, channels: false, blurb: "Enrich people and companies; search the B2B database.", brand_color: "#fbbf24" },
    ConnectorSpec { name: "hunter", title: "Hunter", logo: "hunter", auth: "api_token", two_way: false, channels: false, blurb: "Find and verify professional email addresses by domain.", brand_color: "#fa5320" },
    ConnectorSpec { name: "pagerduty", title: "PagerDuty", logo: "pagerduty", auth: "none", two_way: false, channels: false, blurb: "See who's on-call and review active incidents before paging.", brand_color: "#06ac38" },
];

/// One entry in the Connector Catalog Authority.
#[derive(Clone, Copy)]
struct ConnectorSpec {
    name: &'static str,
    title: &'static str,
    logo: &'static str,
    auth: &'static str,
    two_way: bool,
    channels: bool,
    blurb: &'static str,
    brand_color: &'static str,
}

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

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ApplicationAuthority {
    #[serde(default)]
    state: ApplicationState,
    #[serde(default)]
    secrets: BTreeMap<String, BTreeMap<String, String>>,
}

pub struct ApplicationStore {
    authority_path: PathBuf,
}

impl ApplicationStore {
    pub fn open(state_dir: impl AsRef<Path>) -> Result<Self, ShadowReadError> {
        std::fs::create_dir_all(state_dir.as_ref())?;
        let store = Self {
            authority_path: state_dir.as_ref().join("application.json"),
        };
        store.migrate_legacy(state_dir.as_ref())?;
        Ok(store)
    }

    fn migrate_legacy(&self, state_dir: &Path) -> Result<(), ShadowReadError> {
        let legacy_state = state_dir.join("application-state.json");
        let legacy_secrets = state_dir.join("connector-secrets.json");

        if self.authority_path.exists() {
            // Once the consolidated file exists it is the sole authority.
            // Validate it before removing stale migration inputs.
            self.read_authority()?;
            remove_if_exists(&legacy_state)?;
            remove_if_exists(&legacy_secrets)?;
            return Ok(());
        }
        if !legacy_state.exists() && !legacy_secrets.exists() {
            return Ok(());
        }

        let state = if legacy_state.exists() {
            serde_json::from_str(&std::fs::read_to_string(&legacy_state)?)?
        } else {
            ApplicationState::default()
        };
        let secrets = if legacy_secrets.exists() {
            serde_json::from_str(&std::fs::read_to_string(&legacy_secrets)?)?
        } else {
            BTreeMap::new()
        };
        self.write_authority(&ApplicationAuthority { state, secrets })?;
        remove_if_exists(&legacy_state)?;
        remove_if_exists(&legacy_secrets)?;
        Ok(())
    }

    fn read_authority(&self) -> Result<ApplicationAuthority, ShadowReadError> {
        if !self.authority_path.exists() {
            return Ok(ApplicationAuthority::default());
        }
        let text = std::fs::read_to_string(&self.authority_path)?;
        Ok(serde_json::from_str(&text)?)
    }

    fn write_authority(&self, authority: &ApplicationAuthority) -> Result<(), ShadowReadError> {
        let bytes = serde_json::to_vec_pretty(authority)?;
        atomic_write_private(&self.authority_path, &bytes)?;
        Ok(())
    }

    fn read(&self) -> Result<ApplicationState, ShadowReadError> {
        Ok(self.read_authority()?.state)
    }

    fn write(&self, state: &ApplicationState) -> Result<(), ShadowReadError> {
        let mut authority = self.read_authority()?;
        authority.state = state.clone();
        self.write_authority(&authority)
    }

    pub fn connectors(&self) -> Result<Vec<Value>, ShadowReadError> {
        let state = self.read()?;
        Ok(CONNECTORS
            .iter()
            .map(|spec| {
                let current = state.connectors.get(spec.name).cloned().unwrap_or_default();
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
                    "name": spec.name, "title": spec.title, "icon": spec.logo, "logo": spec.logo,
                    "blurb": spec.blurb, "about": "", "access": [],
                    "auth": spec.auth, "two_way": spec.two_way, "channels": spec.channels,
                    "available": true, "fields": [], "instructions": [],
                    "connected": current.connected, "account": current.account,
                    "enabled": current.enabled, "brand_color": spec.brand_color,
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
            .collect())
    }

    pub fn connect(
        &self,
        name: &str,
        fields: &BTreeMap<String, String>,
    ) -> Result<Value, ShadowReadError> {
        if !CONNECTORS.iter().any(|descriptor| descriptor.name == name) {
            return Ok(json!({"ok": false, "error": "unknown connector"}));
        }
        if fields.is_empty() && name != "browser" {
            return Ok(json!({"ok": false, "error": "connector credentials are required"}));
        }
        let mut authority = self.read_authority()?;
        authority.secrets.insert(name.to_string(), fields.clone());
        let account = fields
            .get("email")
            .or_else(|| fields.get("account"))
            .or_else(|| fields.get("workspace"))
            .cloned()
            .unwrap_or_else(|| "Connected".to_string());
        let previous = authority.state.connectors.remove(name).unwrap_or_default();
        authority.state.connectors.insert(
            name.to_string(),
            ConnectorState {
                connected: true,
                enabled: true,
                account: Some(account.clone()),
                tools: previous.tools,
                details: previous.details,
            },
        );
        self.write_authority(&authority)?;
        Ok(json!({"ok": true, "account": account}))
    }

    pub fn disconnect(&self, name: &str) -> Result<Value, ShadowReadError> {
        let mut authority = self.read_authority()?;
        let removed = authority.state.connectors.remove(name).is_some();
        authority.secrets.remove(name);
        self.write_authority(&authority)?;
        Ok(json!({"ok": removed}))
    }

    pub fn update_tools(
        &self,
        name: &str,
        enabled: &BTreeMap<String, bool>,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read()?;
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
        let mut state = self.read()?;
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

    pub fn session_connections(&self, session_id: &str) -> Result<Value, ShadowReadError> {
        let state = self.read()?;
        let overrides = state.session_connections.get(session_id);
        let connected = state.connectors.iter().filter(|(_, connector)| connector.connected)
            .map(|(name, connector)| json!({
                "connector": name,
                "enabled": overrides.and_then(|values| values.get(name)).copied().unwrap_or(connector.enabled),
                "detail": connector.account.clone().unwrap_or_default(),
            })).collect::<Vec<_>>();
        Ok(json!({"connected": connected, "recommended": [], "attention": 0}))
    }

    pub fn set_session_connection(
        &self,
        session_id: &str,
        connector: &str,
        enabled: bool,
        clear: bool,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read()?;
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

    pub fn subscriptions(&self) -> Result<Vec<Value>, ShadowReadError> {
        Ok(self.read()?.subscriptions)
    }

    pub fn subscribe(&self, session_id: &str, channel: &str) -> Result<Value, ShadowReadError> {
        if session_id.is_empty() || channel.trim().is_empty() {
            return Ok(json!({"ok": false, "error": "session and channel are required"}));
        }
        let mut state = self.read()?;
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
        let mut state = self.read()?;
        let before = state.subscriptions.len();
        state.subscriptions.retain(|item| {
            item.get("session_id").and_then(Value::as_str) != Some(session_id)
                || item.get("channel").and_then(Value::as_str) != Some(channel)
        });
        let removed = state.subscriptions.len() != before;
        self.write(&state)?;
        Ok(json!({"ok": true, "removed": removed}))
    }

    pub fn inbox_bindings(&self) -> Result<Vec<Value>, ShadowReadError> {
        Ok(self.read()?.inbox_bindings)
    }

    pub fn set_inbox_binding(
        &self,
        name: &str,
        channel: Option<&str>,
        target: &str,
    ) -> Result<Value, ShadowReadError> {
        let mut state = self.read()?;
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

    pub fn unrouted(&self) -> Result<Vec<Value>, ShadowReadError> {
        Ok(self.read()?.unrouted)
    }
    pub fn recent_channels(&self) -> Result<Vec<Value>, ShadowReadError> {
        Ok(self.read()?.recent_channels)
    }
    pub fn dm_route(&self) -> Result<Option<String>, ShadowReadError> {
        Ok(self.read()?.dm_session)
    }

    pub fn set_dm_route(&self, session_id: &str) -> Result<Value, ShadowReadError> {
        let mut state = self.read()?;
        state.dm_session = (!session_id.is_empty()).then(|| session_id.to_string());
        let route = state.dm_session.clone();
        self.write(&state)?;
        Ok(json!({"ok": true, "dm_session": route}))
    }

    pub fn connected_names(&self) -> Result<BTreeSet<String>, ShadowReadError> {
        Ok(self
            .read()?
            .connectors
            .into_iter()
            .filter(|(_, state)| state.connected && state.enabled)
            .map(|(name, _)| name)
            .collect())
    }
}

fn remove_if_exists(path: &Path) -> Result<(), ShadowReadError> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
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
        assert!(!serde_json::to_string(&store.connectors().unwrap())
            .unwrap()
            .contains("secret-value"));
        assert!(store.connected_names().unwrap().contains("slack"));
    }

    #[test]
    fn subscriptions_are_idempotent_and_removable() {
        let temp = tempfile::tempdir().unwrap();
        let store = ApplicationStore::open(temp.path()).unwrap();
        store.subscribe("s1", "slack:C1").unwrap();
        store.subscribe("s1", "slack:C1").unwrap();
        assert_eq!(store.subscriptions().unwrap().len(), 1);
        assert_eq!(
            store.unsubscribe("s1", "slack:C1").unwrap()["removed"],
            true
        );
    }

    #[test]
    fn corrupt_consolidated_authority_fails_closed_and_is_not_overwritten() {
        let temp = tempfile::tempdir().unwrap();
        let authority_path = temp.path().join("application.json");
        std::fs::write(&authority_path, b"{not-json").unwrap();

        assert!(matches!(
            ApplicationStore::open(temp.path()),
            Err(ShadowReadError::Json(_))
        ));
        assert_eq!(std::fs::read(&authority_path).unwrap(), b"{not-json");
    }

    #[test]
    fn legacy_state_and_secrets_migrate_once_into_single_authority() {
        let temp = tempfile::tempdir().unwrap();
        let mut state = ApplicationState::default();
        state.connectors.insert(
            "slack".to_string(),
            ConnectorState {
                connected: true,
                enabled: true,
                account: Some("legacy".to_string()),
                ..ConnectorState::default()
            },
        );
        let secrets = BTreeMap::from([(
            "slack".to_string(),
            BTreeMap::from([("token".to_string(), "legacy-secret".to_string())]),
        )]);
        let legacy_state = temp.path().join("application-state.json");
        let legacy_secrets = temp.path().join("connector-secrets.json");
        std::fs::write(&legacy_state, serde_json::to_vec_pretty(&state).unwrap()).unwrap();
        std::fs::write(
            &legacy_secrets,
            serde_json::to_vec_pretty(&secrets).unwrap(),
        )
        .unwrap();

        let store = ApplicationStore::open(temp.path()).unwrap();
        assert!(!legacy_state.exists());
        assert!(!legacy_secrets.exists());
        let authority = store.read_authority().unwrap();
        assert!(authority.state.connectors["slack"].connected);
        assert_eq!(authority.secrets["slack"]["token"], "legacy-secret");
        assert!(temp.path().join("application.json").is_file());
    }

    #[test]
    fn consolidated_authority_wins_after_migration_commit_point() {
        let temp = tempfile::tempdir().unwrap();
        let authority = ApplicationAuthority {
            state: ApplicationState::default(),
            secrets: BTreeMap::new(),
        };
        atomic_write_private(
            temp.path().join("application.json"),
            &serde_json::to_vec_pretty(&authority).unwrap(),
        )
        .unwrap();
        std::fs::write(temp.path().join("application-state.json"), b"{stale").unwrap();
        std::fs::write(temp.path().join("connector-secrets.json"), b"{stale").unwrap();

        ApplicationStore::open(temp.path()).unwrap();
        assert!(!temp.path().join("application-state.json").exists());
        assert!(!temp.path().join("connector-secrets.json").exists());
    }

    #[test]
    fn corrupt_legacy_state_fails_migration_without_creating_new_authority() {
        let temp = tempfile::tempdir().unwrap();
        let legacy_state = temp.path().join("application-state.json");
        std::fs::write(&legacy_state, b"{not-json").unwrap();

        assert!(matches!(
            ApplicationStore::open(temp.path()),
            Err(ShadowReadError::Json(_))
        ));
        assert_eq!(std::fs::read(&legacy_state).unwrap(), b"{not-json");
        assert!(!temp.path().join("application.json").exists());
    }

    #[test]
    fn corrupt_legacy_secrets_fail_migration_without_mutating_legacy_state() {
        let temp = tempfile::tempdir().unwrap();
        let legacy_state = temp.path().join("application-state.json");
        let legacy_secrets = temp.path().join("connector-secrets.json");
        let state_bytes = serde_json::to_vec_pretty(&ApplicationState::default()).unwrap();
        std::fs::write(&legacy_state, &state_bytes).unwrap();
        std::fs::write(&legacy_secrets, b"{not-json").unwrap();

        assert!(matches!(
            ApplicationStore::open(temp.path()),
            Err(ShadowReadError::Json(_))
        ));
        assert_eq!(std::fs::read(&legacy_state).unwrap(), state_bytes);
        assert_eq!(std::fs::read(&legacy_secrets).unwrap(), b"{not-json");
        assert!(!temp.path().join("application.json").exists());
    }

    #[test]
    fn connect_and_disconnect_commit_state_and_secrets_together() {
        let temp = tempfile::tempdir().unwrap();
        let store = ApplicationStore::open(temp.path()).unwrap();
        store
            .connect(
                "slack",
                &BTreeMap::from([("token".to_string(), "secret-value".to_string())]),
            )
            .unwrap();
        let connected = store.read_authority().unwrap();
        assert!(connected.state.connectors["slack"].connected);
        assert_eq!(connected.secrets["slack"]["token"], "secret-value");

        store.disconnect("slack").unwrap();
        let disconnected = store.read_authority().unwrap();
        assert!(!disconnected.state.connectors.contains_key("slack"));
        assert!(!disconnected.secrets.contains_key("slack"));
    }

    #[cfg(unix)]
    #[test]
    fn application_authority_is_persisted_private() {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir().unwrap();
        let store = ApplicationStore::open(temp.path()).unwrap();
        store
            .connect(
                "slack",
                &BTreeMap::from([("token".to_string(), "secret-value".to_string())]),
            )
            .unwrap();
        let mode = std::fs::metadata(temp.path().join("application.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
    }

    #[test]
    fn connector_catalog_authority_serves_real_metadata_and_hides_secrets() {
        let temp = tempfile::tempdir().unwrap();
        let store = ApplicationStore::open(temp.path()).unwrap();
        store
            .connect(
                "slack",
                &BTreeMap::from([("token".to_string(), "top-secret".to_string())]),
            )
            .unwrap();
        let rows = store.connectors().unwrap();
        let slack = rows
            .iter()
            .find(|row| row["name"] == "slack")
            .expect("slack in catalog");
        // Real business metadata (not placeholders) served from the single
        // authority: brand color, blurb, auth requirement, two-way flag.
        assert_eq!(slack["brand_color"], "#611f69");
        assert_eq!(slack["auth"], "socket_app");
        assert_eq!(slack["two_way"], true);
        assert!(slack["blurb"].as_str().unwrap().contains("Socket Mode"));
        // The catalog never exposes secret values.
        assert!(!slack.to_string().contains("top-secret"));
        // Every connector row carries a non-placeholder brand color.
        assert!(rows
            .iter()
            .all(|row| row["brand_color"].as_str().is_some_and(|c| !c.is_empty() && c != "#6b7280" || row["name"] == "email")));
    }
}
