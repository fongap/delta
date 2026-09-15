from pathlib import Path

memory = Path("core/runtime-native/src/memory.rs")
text = memory.read_text(encoding="utf-8")

old = '''    pub fn settings(&self) -> Value {
        let saved = std::fs::read_to_string(&self.settings_path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .unwrap_or_else(|| json!({}));
        json!({
            "enabled": saved.get("enabled").and_then(Value::as_bool).unwrap_or(true),
            "user_rules": saved.get("user_rules").and_then(Value::as_str).unwrap_or_default(),
        })
    }

    pub fn set_settings(&self, patch: &Value) -> Result<Value, ShadowReadError> {
        let mut settings = self.settings();
'''

new = '''    pub fn settings(&self) -> Result<Value, ShadowReadError> {
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
'''

if old not in text:
    raise SystemExit("memory settings block not found")
text = text.replace(old, new, 1)

marker = '''    #[test]
    fn memory_crud_and_settings_are_rust_owned() {
'''
if marker not in text:
    raise SystemExit("memory test marker not found")

tests = '''    #[test]
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
        for invalid in [
            "[]",
            r#"{"enabled":"yes"}"#,
            r#"{"user_rules":42}"#,
        ] {
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

'''
text = text.replace(marker, tests + marker, 1)
memory.write_text(text, encoding="utf-8")

ipc = Path("apps/desktop/src-tauri/src/runtime_ipc.rs")
ipc_text = ipc.read_text(encoding="utf-8")
old_ipc = '''pub fn memory_settings(state: State<'_, RuntimeRegistry>) -> Value {
    state.memory.settings()
}
'''
new_ipc = '''pub fn memory_settings(state: State<'_, RuntimeRegistry>) -> Value {
    authority_result(state.memory.settings())
}
'''
if old_ipc not in ipc_text:
    raise SystemExit("memory_settings IPC block not found")
ipc.write_text(ipc_text.replace(old_ipc, new_ipc, 1), encoding="utf-8")
