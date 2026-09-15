from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


path = Path("core/runtime-native/src/application.rs")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''        let mut secrets = self.read_secrets()?;\n        secrets.insert(name.to_string(), fields.clone());\n        self.write_secrets(&secrets)?;\n        let account = fields\n            .get("email")\n            .or_else(|| fields.get("account"))\n            .or_else(|| fields.get("workspace"))\n            .cloned()\n            .unwrap_or_else(|| "Connected".to_string());\n        let mut state = self.read()?;\n        let previous = state.connectors.remove(name).unwrap_or_default();\n''',
    '''        // Validate both authoritative files before mutating either one.\n        let mut state = self.read()?;\n        let mut secrets = self.read_secrets()?;\n        secrets.insert(name.to_string(), fields.clone());\n        let account = fields\n            .get("email")\n            .or_else(|| fields.get("account"))\n            .or_else(|| fields.get("workspace"))\n            .cloned()\n            .unwrap_or_else(|| "Connected".to_string());\n        let previous = state.connectors.remove(name).unwrap_or_default();\n''',
    "connect preflight",
)
text = replace_once(
    text,
    '''        self.write(&state)?;\n        Ok(json!({"ok": true, "account": account}))\n    }\n\n    pub fn disconnect(&self, name: &str) -> Result<Value, ShadowReadError> {\n        let mut state = self.read()?;\n        let removed = state.connectors.remove(name).is_some();\n        self.write(&state)?;\n        let mut secrets = self.read_secrets()?;\n        secrets.remove(name);\n        self.write_secrets(&secrets)?;\n''',
    '''        self.write_secrets(&secrets)?;\n        self.write(&state)?;\n        Ok(json!({"ok": true, "account": account}))\n    }\n\n    pub fn disconnect(&self, name: &str) -> Result<Value, ShadowReadError> {\n        // Validate both authoritative files before mutating either one.\n        let mut state = self.read()?;\n        let mut secrets = self.read_secrets()?;\n        let removed = state.connectors.remove(name).is_some();\n        secrets.remove(name);\n        self.write(&state)?;\n        self.write_secrets(&secrets)?;\n''',
    "disconnect preflight",
)

insert = '''\n    #[test]\n    fn connect_does_not_mutate_secrets_when_application_state_is_corrupt() {\n        let temp = tempfile::tempdir().unwrap();\n        let state_path = temp.path().join("application-state.json");\n        std::fs::write(&state_path, b"{not-json").unwrap();\n        let store = ApplicationStore::open(temp.path()).unwrap();\n\n        assert!(matches!(\n            store.connect(\n                "slack",\n                &BTreeMap::from([("token".to_string(), "new-secret".to_string())]),\n            ),\n            Err(ShadowReadError::Json(_))\n        ));\n        assert!(!temp.path().join("connector-secrets.json").exists());\n    }\n\n    #[test]\n    fn disconnect_does_not_mutate_state_when_secrets_are_corrupt() {\n        let temp = tempfile::tempdir().unwrap();\n        let store = ApplicationStore::open(temp.path()).unwrap();\n        store\n            .connect(\n                "slack",\n                &BTreeMap::from([("token".to_string(), "secret-value".to_string())]),\n            )\n            .unwrap();\n\n        let state_path = temp.path().join("application-state.json");\n        let before = std::fs::read(&state_path).unwrap();\n        std::fs::write(temp.path().join("connector-secrets.json"), b"{not-json").unwrap();\n\n        assert!(matches!(\n            store.disconnect("slack"),\n            Err(ShadowReadError::Json(_))\n        ));\n        assert_eq!(std::fs::read(&state_path).unwrap(), before);\n    }\n'''
pos = text.rfind("\n}")
if pos == -1:
    raise SystemExit("tests close not found")
text = text[:pos] + insert + text[pos:]

path.write_text(text, encoding="utf-8")
