from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


app_path = Path("core/runtime-native/src/application.rs")
text = app_path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''    fn read(&self) -> ApplicationState {\n        std::fs::read_to_string(&self.state_path)\n            .ok()\n            .and_then(|text| serde_json::from_str(&text).ok())\n            .unwrap_or_default()\n    }\n\n    fn write(&self, state: &ApplicationState) -> Result<(), ShadowReadError> {\n        std::fs::write(&self.state_path, serde_json::to_vec_pretty(state)?)?;\n        Ok(())\n    }\n''',
    '''    fn read(&self) -> Result<ApplicationState, ShadowReadError> {\n        if !self.state_path.exists() {\n            return Ok(ApplicationState::default());\n        }\n        let text = std::fs::read_to_string(&self.state_path)?;\n        Ok(serde_json::from_str(&text)?)\n    }\n\n    fn write(&self, state: &ApplicationState) -> Result<(), ShadowReadError> {\n        std::fs::write(&self.state_path, serde_json::to_vec_pretty(state)?)?;\n        Ok(())\n    }\n\n    fn read_secrets(&self) -> Result<BTreeMap<String, BTreeMap<String, String>>, ShadowReadError> {\n        if !self.secrets_path.exists() {\n            return Ok(BTreeMap::new());\n        }\n        let text = std::fs::read_to_string(&self.secrets_path)?;\n        Ok(serde_json::from_str(&text)?)\n    }\n\n    fn write_secrets(\n        &self,\n        secrets: &BTreeMap<String, BTreeMap<String, String>>,\n    ) -> Result<(), ShadowReadError> {\n        std::fs::write(&self.secrets_path, serde_json::to_vec_pretty(secrets)?)?;\n        restrict_private_file(&self.secrets_path)?;\n        Ok(())\n    }\n''',
    "read helpers",
)

text = replace_once(
    text,
    '''    pub fn connectors(&self) -> Vec<Value> {\n        let state = self.read();\n        CONNECTORS\n''',
    '''    pub fn connectors(&self) -> Result<Vec<Value>, ShadowReadError> {\n        let state = self.read()?;\n        Ok(CONNECTORS\n''',
    "connectors start",
)
text = replace_once(
    text,
    '''            })\n            .collect()\n    }\n\n    pub fn connect(\n''',
    '''            })\n            .collect())\n    }\n\n    pub fn connect(\n''',
    "connectors end",
)

text = replace_once(
    text,
    '''        let mut secrets = std::fs::read_to_string(&self.secrets_path)\n            .ok()\n            .and_then(|text| {\n                serde_json::from_str::<BTreeMap<String, BTreeMap<String, String>>>(&text).ok()\n            })\n            .unwrap_or_default();\n        secrets.insert(name.to_string(), fields.clone());\n        std::fs::write(&self.secrets_path, serde_json::to_vec_pretty(&secrets)?)?;\n''',
    '''        let mut secrets = self.read_secrets()?;\n        secrets.insert(name.to_string(), fields.clone());\n        self.write_secrets(&secrets)?;\n''',
    "connect secrets",
)

text = text.replace("let mut state = self.read();", "let mut state = self.read()?;")

text = replace_once(
    text,
    '''        let mut secrets = std::fs::read_to_string(&self.secrets_path)\n            .ok()\n            .and_then(|text| serde_json::from_str::<BTreeMap<String, Value>>(&text).ok())\n            .unwrap_or_default();\n        secrets.remove(name);\n        std::fs::write(&self.secrets_path, serde_json::to_vec_pretty(&secrets)?)?;\n''',
    '''        let mut secrets = self.read_secrets()?;\n        secrets.remove(name);\n        self.write_secrets(&secrets)?;\n''',
    "disconnect secrets",
)

text = replace_once(
    text,
    '''    pub fn session_connections(&self, session_id: &str) -> Value {\n        let state = self.read();\n''',
    '''    pub fn session_connections(&self, session_id: &str) -> Result<Value, ShadowReadError> {\n        let state = self.read()?;\n''',
    "session_connections start",
)
text = replace_once(
    text,
    '''        json!({"connected": connected, "recommended": [], "attention": 0})\n    }\n''',
    '''        Ok(json!({"connected": connected, "recommended": [], "attention": 0}))\n    }\n''',
    "session_connections end",
)

for old, new, label in [
    ('''    pub fn subscriptions(&self) -> Vec<Value> {\n        self.read().subscriptions\n    }\n''', '''    pub fn subscriptions(&self) -> Result<Vec<Value>, ShadowReadError> {\n        Ok(self.read()?.subscriptions)\n    }\n''', "subscriptions"),
    ('''    pub fn inbox_bindings(&self) -> Vec<Value> {\n        self.read().inbox_bindings\n    }\n''', '''    pub fn inbox_bindings(&self) -> Result<Vec<Value>, ShadowReadError> {\n        Ok(self.read()?.inbox_bindings)\n    }\n''', "inbox_bindings"),
    ('''    pub fn unrouted(&self) -> Vec<Value> {\n        self.read().unrouted\n    }\n    pub fn recent_channels(&self) -> Vec<Value> {\n        self.read().recent_channels\n    }\n    pub fn dm_route(&self) -> Option<String> {\n        self.read().dm_session\n    }\n''', '''    pub fn unrouted(&self) -> Result<Vec<Value>, ShadowReadError> {\n        Ok(self.read()?.unrouted)\n    }\n    pub fn recent_channels(&self) -> Result<Vec<Value>, ShadowReadError> {\n        Ok(self.read()?.recent_channels)\n    }\n    pub fn dm_route(&self) -> Result<Option<String>, ShadowReadError> {\n        Ok(self.read()?.dm_session)\n    }\n''', "routing reads"),
    ('''    pub fn connected_names(&self) -> BTreeSet<String> {\n        self.read()\n            .connectors\n            .into_iter()\n            .filter(|(_, state)| state.connected && state.enabled)\n            .map(|(name, _)| name)\n            .collect()\n    }\n''', '''    pub fn connected_names(&self) -> Result<BTreeSet<String>, ShadowReadError> {\n        Ok(self\n            .read()?\n            .connectors\n            .into_iter()\n            .filter(|(_, state)| state.connected && state.enabled)\n            .map(|(name, _)| name)\n            .collect())\n    }\n''', "connected_names"),
]:
    text = replace_once(text, old, new, label)

# Tests now unwrap fallible read APIs.
text = text.replace("serde_json::to_string(&store.connectors())", "serde_json::to_string(&store.connectors().unwrap())")
text = text.replace("store.connected_names().contains(\"slack\")", "store.connected_names().unwrap().contains(\"slack\")")
text = text.replace("store.subscriptions().len()", "store.subscriptions().unwrap().len()")

insert = '''\n    #[test]\n    fn corrupt_application_state_fails_closed_and_is_not_overwritten() {\n        let temp = tempfile::tempdir().unwrap();\n        let state_path = temp.path().join("application-state.json");\n        std::fs::write(&state_path, b"{not-json").unwrap();\n        let store = ApplicationStore::open(temp.path()).unwrap();\n\n        assert!(matches!(store.connectors(), Err(ShadowReadError::Json(_))));\n        assert!(matches!(\n            store.update_tools("slack", &BTreeMap::from([("send".to_string(), true)])),\n            Err(ShadowReadError::Json(_))\n        ));\n        assert_eq!(std::fs::read(&state_path).unwrap(), b"{not-json");\n    }\n\n    #[test]\n    fn corrupt_connector_secrets_fail_closed_and_are_not_overwritten() {\n        let temp = tempfile::tempdir().unwrap();\n        let secrets_path = temp.path().join("connector-secrets.json");\n        std::fs::write(&secrets_path, b"{not-json").unwrap();\n        let store = ApplicationStore::open(temp.path()).unwrap();\n\n        assert!(matches!(\n            store.connect(\n                "slack",\n                &BTreeMap::from([("token".to_string(), "new-secret".to_string())]),\n            ),\n            Err(ShadowReadError::Json(_))\n        ));\n        assert_eq!(std::fs::read(&secrets_path).unwrap(), b"{not-json");\n    }\n\n    #[cfg(unix)]\n    #[test]\n    fn connector_secrets_are_persisted_private() {\n        use std::os::unix::fs::PermissionsExt;\n\n        let temp = tempfile::tempdir().unwrap();\n        let store = ApplicationStore::open(temp.path()).unwrap();\n        store\n            .connect(\n                "slack",\n                &BTreeMap::from([("token".to_string(), "secret-value".to_string())]),\n            )\n            .unwrap();\n        let mode = std::fs::metadata(temp.path().join("connector-secrets.json"))\n            .unwrap()\n            .permissions()\n            .mode()\n            & 0o777;\n        assert_eq!(mode, 0o600);\n    }\n'''
text = replace_once(text, "\n}\n", insert + "\n}\n", "tests module close") if False else text
# Insert before the final tests-module brace only.
pos = text.rfind("\n}")
if pos == -1:
    raise SystemExit("tests close not found")
text = text[:pos] + insert + text[pos:]

# Add private-file helper before tests.
helper = '''\n#[cfg(unix)]\nfn restrict_private_file(path: &Path) -> Result<(), ShadowReadError> {\n    use std::os::unix::fs::PermissionsExt;\n    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;\n    Ok(())\n}\n\n#[cfg(windows)]\nfn restrict_private_file(_path: &Path) -> Result<(), ShadowReadError> {\n    Ok(())\n}\n\n#[cfg(not(any(unix, windows)))]\nfn restrict_private_file(_path: &Path) -> Result<(), ShadowReadError> {\n    Ok(())\n}\n\n'''
text = replace_once(text, "\n#[cfg(test)]\nmod tests {", helper + "#[cfg(test)]\nmod tests {", "private helper")

app_path.write_text(text, encoding="utf-8")

ipc_path = Path("apps/desktop/src-tauri/src/runtime_ipc.rs")
ipc = ipc_path.read_text(encoding="utf-8")
repls = [
    ('''pub fn connectors_list(state: State<'_, RuntimeRegistry>) -> Value {\n    json!({"connectors": state.application.connectors()})\n}''', '''pub fn connectors_list(state: State<'_, RuntimeRegistry>) -> Result<Value, String> {\n    state\n        .application\n        .connectors()\n        .map(|connectors| json!({"connectors": connectors}))\n        .map_err(|error| error.to_string())\n}''', "ipc connectors"),
    ('''pub fn session_connections(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {\n    state.application.session_connections(&session_id)\n}''', '''pub fn session_connections(\n    state: State<'_, RuntimeRegistry>,\n    session_id: String,\n) -> Result<Value, String> {\n    state\n        .application\n        .session_connections(&session_id)\n        .map_err(|error| error.to_string())\n}''', "ipc session connections"),
    ('''pub fn subscriptions_list(state: State<'_, RuntimeRegistry>) -> Value {\n    json!({"subscriptions": state.application.subscriptions()})\n}''', '''pub fn subscriptions_list(state: State<'_, RuntimeRegistry>) -> Result<Value, String> {\n    state\n        .application\n        .subscriptions()\n        .map(|subscriptions| json!({"subscriptions": subscriptions}))\n        .map_err(|error| error.to_string())\n}''', "ipc subscriptions"),
    ('''pub fn inbox_routing_list(state: State<'_, RuntimeRegistry>) -> Value {\n    json!({"bindings": state.application.inbox_bindings()})\n}''', '''pub fn inbox_routing_list(state: State<'_, RuntimeRegistry>) -> Result<Value, String> {\n    state\n        .application\n        .inbox_bindings()\n        .map(|bindings| json!({"bindings": bindings}))\n        .map_err(|error| error.to_string())\n}''', "ipc inbox routing"),
    ('''pub fn unrouted_list(state: State<'_, RuntimeRegistry>) -> Value {\n    json!({"items": state.application.unrouted()})\n}''', '''pub fn unrouted_list(state: State<'_, RuntimeRegistry>) -> Result<Value, String> {\n    state\n        .application\n        .unrouted()\n        .map(|items| json!({"items": items}))\n        .map_err(|error| error.to_string())\n}''', "ipc unrouted"),
    ('''pub fn recent_channels(state: State<'_, RuntimeRegistry>) -> Value {\n    json!({"channels": state.application.recent_channels()})\n}''', '''pub fn recent_channels(state: State<'_, RuntimeRegistry>) -> Result<Value, String> {\n    state\n        .application\n        .recent_channels()\n        .map(|channels| json!({"channels": channels}))\n        .map_err(|error| error.to_string())\n}''', "ipc recent channels"),
    ('''pub fn dm_route_get(state: State<'_, RuntimeRegistry>) -> Value {\n    json!({"dm_session": state.application.dm_route()})\n}''', '''pub fn dm_route_get(state: State<'_, RuntimeRegistry>) -> Result<Value, String> {\n    state\n        .application\n        .dm_route()\n        .map(|dm_session| json!({"dm_session": dm_session}))\n        .map_err(|error| error.to_string())\n}''', "ipc dm route"),
]
for old, new, label in repls:
    ipc = replace_once(ipc, old, new, label)
ipc_path.write_text(ipc, encoding="utf-8")
