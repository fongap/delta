from pathlib import Path
import re


def sub_once(text: str, pattern: str, replacement: str, label: str, flags=0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, got {count}")
    return updated


path = Path("core/runtime-native/src/model_authority.rs")
text = path.read_text(encoding="utf-8")

# Public/read helpers become fallible.
text = text.replace("let prefs = self.read_prefs();", "let prefs = self.read_prefs()?;")
text = text.replace("let mut prefs = self.read_prefs();", "let mut prefs = self.read_prefs()?;")
text = text.replace("let mut secrets = self.read_secrets();", "let mut secrets = self.read_secrets()?;")

text = sub_once(text, r"pub fn settings\(&self\) -> Value \{", "pub fn settings(&self) -> Result<Value, String> {", "settings signature")
text = sub_once(text, r"pub fn providers\(&self\) -> Value \{", "pub fn providers(&self) -> Result<Value, String> {", "providers signature")
text = sub_once(text, r"pub fn verify_provider\(&self, name: &str, fields: &Value\) -> Value \{", "pub fn verify_provider(&self, name: &str, fields: &Value) -> Result<Value, String> {", "verify signature")
text = sub_once(text, r"pub fn fetch_models\(&self, name: &str, fields: &Value\) -> Value \{", "pub fn fetch_models(&self, name: &str, fields: &Value) -> Result<Value, String> {", "fetch models signature")
text = sub_once(text, r"fn provider_row\(\n        &self,\n        descriptor: ProviderDescriptor,\n        custom: bool,\n        prefs: &Map<String, Value>,\n    \) -> Value \{", "fn provider_row(\n        &self,\n        descriptor: ProviderDescriptor,\n        custom: bool,\n        prefs: &Map<String, Value>,\n    ) -> Result<Value, String> {", "provider row signature")
text = sub_once(text, r"fn provider_configured\(&self, name: &str, prefs: &Map<String, Value>\) -> bool \{", "fn provider_configured(&self, name: &str, prefs: &Map<String, Value>) -> Result<bool, String> {", "provider configured signature")
text = sub_once(text, r"fn resolved_profile\(&self, name: &str, prefs: &Map<String, Value>\) -> Map<String, Value> \{", "fn resolved_profile(&self, name: &str, prefs: &Map<String, Value>) -> Result<Map<String, Value>, String> {", "resolved profile signature")
text = sub_once(text, r"fn migrate_legacy_profile\(\n        &self,\n        name: &str,\n        secrets: &mut Map<String, Value>,\n        prefs: &Map<String, Value>,\n    \) \{", "fn migrate_legacy_profile(\n        &self,\n        name: &str,\n        secrets: &mut Map<String, Value>,\n        prefs: &Map<String, Value>,\n    ) -> Result<(), String> {", "migration signature")

# Result propagation for profile reads and configured checks.
text = text.replace("self.resolved_profile(&provider_name, &prefs);", "self.resolved_profile(&provider_name, &prefs)?;")
text = text.replace('self.resolved_profile("openai", &prefs);', 'self.resolved_profile("openai", &prefs)?;')
text = text.replace("self.resolved_profile(name, prefs);", "self.resolved_profile(name, prefs)?;")
text = text.replace("self.migrate_legacy_profile(name, &mut secrets, prefs);", "self.migrate_legacy_profile(name, &mut secrets, prefs)?;")

# settings(): configured checks are now fallible and the body returns Ok(json!).
text = text.replace("self.provider_configured(&self.route_model(model, &prefs).0, &prefs)", "self.provider_configured(&self.route_model(model, &prefs).0, &prefs)?")
text = text.replace("self.provider_configured(&self.route_model(&default_model, &prefs).0, &prefs)", "self.provider_configured(&self.route_model(&default_model, &prefs).0, &prefs)?")
settings_start = text.index("    pub fn settings(&self) -> Result<Value, String> {")
settings_end = text.index("\n    pub fn protocols(&self)", settings_start)
settings_block = text[settings_start:settings_end]
settings_block = settings_block.replace("        json!({", "        Ok(json!({", 1)
# close the new Ok wrapper at the final function expression
last = settings_block.rfind("        })\n    }")
if last < 0:
    raise SystemExit("settings close marker missing")
settings_block = settings_block[:last] + "        }))\n    }" + settings_block[last + len("        })\n    }"):]
text = text[:settings_start] + settings_block + text[settings_end:]

# providers(): provider_row is fallible and result is wrapped.
providers_start = text.index("    pub fn providers(&self) -> Result<Value, String> {")
providers_end = text.index("\n    pub fn set_provider(", providers_start)
block = text[providers_start:providers_end]
block = block.replace(
    ".map(|descriptor| self.provider_row((*descriptor).into(), false, &prefs))\n            .collect();",
    ".map(|descriptor| self.provider_row((*descriptor).into(), false, &prefs))\n            .collect::<Result<Vec<_>, _>>()?;",
)
block = block.replace("rows.push(self.provider_row(descriptor, true, &prefs));", "rows.push(self.provider_row(descriptor, true, &prefs)?);")
block = block.replace("        Value::Array(rows)\n    }", "        Ok(Value::Array(rows))\n    }")
text = text[:providers_start] + block + text[providers_end:]

# set_provider default-model readiness check.
text = text.replace(
    "|| !self.provider_configured(&self.route_model(current, &prefs).0, &prefs)",
    "|| !self.provider_configured(&self.route_model(current, &prefs).0, &prefs)?",
)

# settings refresh after writes.
text = text.replace("Ok(with_ok(self.settings()))", "self.settings().map(with_ok)")

# verify/fetch model product responses become Result without changing payload shape.
verify_start = text.index("    pub fn verify_provider(&self, name: &str, fields: &Value) -> Result<Value, String> {")
verify_end = text.index("\n    pub fn fetch_models", verify_start)
verify = text[verify_start:verify_end]
verify = verify.replace("        match self.probe_config", "        Ok(match self.probe_config", 1)
verify = verify.replace("        }\n    }", "        })\n    }", 1)
text = text[:verify_start] + verify + text[verify_end:]

fetch_start = text.index("    pub fn fetch_models(&self, name: &str, fields: &Value) -> Result<Value, String> {")
fetch_end = text.index("\n    fn probe_config", fetch_start)
fetch = text[fetch_start:fetch_end]
fetch = fetch.replace('Err(error) => return json!({"ok": false, "error": error}),', 'Err(error) => return Ok(json!({"ok": false, "error": error})),')
fetch = fetch.replace("            return result;", "            return Ok(result);")
fetch = fetch.replace('        if let Err(error) = self.write_prefs(&prefs) {\n            return json!({"ok": false, "error": error});\n        }', '        self.write_prefs(&prefs)?;')
fetch = fetch.replace('        json!({"ok": true, "alias": name, "models": models, "added": added})\n    }', '        Ok(json!({"ok": true, "alias": name, "models": models, "added": added}))\n    }')
text = text[:fetch_start] + fetch + text[fetch_end:]

# probe_config profile is fallible.
text = text.replace("let mut profile = self.resolved_profile(name, prefs);", "let mut profile = self.resolved_profile(name, prefs)?;")

# provider_row wraps payload in Ok.
row_start = text.index("    fn provider_row(")
row_end = text.index("\n    fn provider_configured", row_start)
row = text[row_start:row_end]
row = row.replace("        json!({", "        Ok(json!({", 1)
row = row.replace("        })\n    }", "        }))\n    }", 1)
text = text[:row_start] + row + text[row_end:]

# provider_configured preserves unknown-provider=false but propagates storage failures.
pc_start = text.index("    fn provider_configured(&self, name: &str, prefs: &Map<String, Value>) -> Result<bool, String> {")
pc_end = text.index("\n    fn profile_api_key", pc_start)
pc = text[pc_start:pc_end]
pc = pc.replace("            return false;", "            return Ok(false);")
pc = pc.replace("        !self\n            .profile_api_key", "        Ok(!self\n            .profile_api_key", 1)
pc = pc.replace("            .is_empty()\n    }", "            .is_empty())\n    }", 1)
text = text[:pc_start] + pc + text[pc_end:]

# resolved_profile wraps map; migration write failures propagate.
rp_start = text.index("    fn resolved_profile(&self, name: &str, prefs: &Map<String, Value>) -> Result<Map<String, Value>, String> {")
rp_end = text.index("\n    fn migrate_legacy_profile", rp_start)
rp = text[rp_start:rp_end]
rp = rp.replace("        secrets\n            .get", "        Ok(secrets\n            .get", 1)
rp = rp.replace("            .unwrap_or_default()\n    }", "            .unwrap_or_default())\n    }", 1)
text = text[:rp_start] + rp + text[rp_end:]

mig_start = text.index("    fn migrate_legacy_profile(")
mig_end = text.index("\n    fn add_model_to_prefs", mig_start)
mig = text[mig_start:mig_end]
mig = mig.replace("                let _ = self.write_secrets(secrets);", "                self.write_secrets(secrets)?;")
# append Ok(()) before function close
close = mig.rfind("    }\n")
if close < 0:
    raise SystemExit("migration close missing")
mig = mig[:close] + "        Ok(())\n" + mig[close:]
text = text[:mig_start] + mig + text[mig_end:]

# Strict file reads: missing is first-run empty; existing invalid data is an error.
text = sub_once(
    text,
    r"    fn read_prefs\(&self\) -> Map<String, Value> \{\n        read_object\(&self\.prefs_path\(\)\)\n    \}",
    "    fn read_prefs(&self) -> Result<Map<String, Value>, String> {\n        read_object(&self.prefs_path())\n    }",
    "read_prefs",
)
text = sub_once(
    text,
    r"    fn read_secrets\(&self\) -> Map<String, Value> \{\n        read_object\(&self\.secrets_path\(\)\)\n    \}",
    "    fn read_secrets(&self) -> Result<Map<String, Value>, String> {\n        read_object(&self.secrets_path())\n    }",
    "read_secrets",
)
text = sub_once(
    text,
    r"fn read_object\(path: &Path\) -> Map<String, Value> \{.*?\n\}",
    '''fn read_object(path: &Path) -> Result<Map<String, Value>, String> {
    if !path.exists() {
        return Ok(Map::new());
    }
    let text = fs::read_to_string(path)
        .map_err(|error| format!("read {}: {error}", path.display()))?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|error| format!("parse {}: {error}", path.display()))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| format!("{} must contain a JSON object", path.display()))
}''',
    "read_object",
    re.S,
)

# Tests adapt to Result and add corruption coverage.
text = text.replace("let settings = authority.settings();", "let settings = authority.settings().unwrap();")
text = text.replace("let providers = authority.providers();", "let providers = authority.providers().unwrap();")
text = text.replace(".settings()\n            .to_string()", ".settings()\n            .unwrap()\n            .to_string()")

insert = r'''
    #[test]
    fn corrupt_prefs_fail_closed_for_settings_and_runtime_resolution() {
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join("prefs.json"), b"{not-json").unwrap();
        let authority = ModelAuthority::new(temp.path());

        assert!(authority.settings().is_err());
        assert!(authority.providers().is_err());
        assert!(authority.resolve_runtime_config("gpt-5.6-sol").is_err());
        assert_eq!(fs::read(temp.path().join("prefs.json")).unwrap(), b"{not-json");
    }

    #[test]
    fn corrupt_secrets_fail_closed_and_are_not_overwritten() {
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join("secrets.json"), b"{not-json").unwrap();
        let authority = ModelAuthority::new(temp.path());

        assert!(authority.settings().is_err());
        assert!(authority
            .set_provider(
                "openai",
                None,
                &json!({"api_key": "new-secret", "base_url": DEFAULT_OPENAI_URL}),
            )
            .is_err());
        assert_eq!(fs::read(temp.path().join("secrets.json")).unwrap(), b"{not-json");
    }

    #[test]
    fn non_object_authority_files_fail_closed() {
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join("prefs.json"), b"[]").unwrap();
        let authority = ModelAuthority::new(temp.path());
        assert!(authority.settings().is_err());
    }
'''
pos = text.rfind("\n}")
if pos < 0:
    raise SystemExit("tests close missing")
text = text[:pos] + insert + text[pos:]

path.write_text(text, encoding="utf-8")

# Desktop boundary: state corruption becomes an explicit authority error.
ipc_path = Path("apps/desktop/src-tauri/src/runtime_ipc.rs")
ipc = ipc_path.read_text(encoding="utf-8")
ipc = ipc.replace("let settings = registry.models.lock().unwrap().settings();", "let settings = registry.models.lock().unwrap().settings()?;")
ipc = ipc.replace(
    '''                let model_id = state
                    .models
                    .lock()
                    .unwrap()
                    .settings()
                    .get("model")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();''',
    '''                let settings = state.models.lock().unwrap().settings();
                let model_id = settings
                    .as_ref()
                    .ok()
                    .and_then(|settings| settings.get("model"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();''',
)
ipc = ipc.replace(
    '''                let accepted = match session_ready {
                    Ok(_) => start_runtime(''',
    '''                let accepted = match (settings, session_ready) {
                    (Err(error), _) => json!({"ok": false, "error": error}),
                    (Ok(_), Ok(_)) => start_runtime(''',
)
ipc = ipc.replace(
    '''                    Err(error) => json!({"ok": false, "error": error.to_string()}),
                };''',
    '''                    (_, Err(error)) => json!({"ok": false, "error": error.to_string()}),
                };''',
    1,
)
ipc = ipc.replace(
    '''pub fn settings_get(state: State<'_, RuntimeRegistry>) -> Value {
    state.models.lock().unwrap().settings()
}''',
    '''pub fn settings_get(state: State<'_, RuntimeRegistry>) -> Value {
    authority_result(state.models.lock().unwrap().settings())
}''',
)
ipc = ipc.replace(
    '''pub fn providers_list(state: State<'_, RuntimeRegistry>) -> Value {
    state.models.lock().unwrap().providers()
}''',
    '''pub fn providers_list(state: State<'_, RuntimeRegistry>) -> Value {
    authority_result(state.models.lock().unwrap().providers())
}''',
)
ipc = ipc.replace(
    '''pub fn provider_verify(state: State<'_, RuntimeRegistry>, name: String, fields: Value) -> Value {
    state.models.lock().unwrap().verify_provider(&name, &fields)
}''',
    '''pub fn provider_verify(state: State<'_, RuntimeRegistry>, name: String, fields: Value) -> Value {
    authority_result(state.models.lock().unwrap().verify_provider(&name, &fields))
}''',
)
ipc = ipc.replace(
    '''    state.models.lock().unwrap().fetch_models(&name, &fields)
}''',
    '''    authority_result(state.models.lock().unwrap().fetch_models(&name, &fields))
}''',
    1,
)
ipc_path.write_text(ipc, encoding="utf-8")
