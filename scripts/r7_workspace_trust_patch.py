from pathlib import Path

path = Path("core/runtime-native/src/control_plane.rs")
text = path.read_text(encoding="utf-8")

old_helpers = '''fn trusted_workspace_paths(state_dir: &Path) -> Vec<String> {
    fs::read_to_string(trust_path(state_dir))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.get("trusted_workspaces").cloned())
        .and_then(|value| value.as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| value.as_str().map(str::to_string))
        .collect()
}
'''

new_helpers = '''fn trusted_workspace_paths(state_dir: &Path) -> Result<Vec<String>, ShadowReadError> {
    let path = trust_path(state_dir);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(&path).map_err(|error| {
        ShadowReadError::Parse(format!(
            "failed to read workspace trust authority {}: {error}",
            path.display()
        ))
    })?;
    let value = serde_json::from_str::<Value>(&text).map_err(|error| {
        ShadowReadError::Parse(format!(
            "invalid workspace trust authority {}: {error}",
            path.display()
        ))
    })?;
    let object = value.as_object().ok_or_else(|| {
        ShadowReadError::Parse(format!(
            "workspace trust authority {} must be a JSON object",
            path.display()
        ))
    })?;
    let entries = object
        .get("trusted_workspaces")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            ShadowReadError::Parse(format!(
                "workspace trust authority {} must contain a trusted_workspaces array",
                path.display()
            ))
        })?;
    let mut paths = Vec::with_capacity(entries.len());
    for entry in entries {
        let value = entry.as_str().ok_or_else(|| {
            ShadowReadError::Parse(format!(
                "workspace trust authority {} contains a non-string workspace path",
                path.display()
            ))
        })?;
        paths.push(value.to_string());
    }
    Ok(paths)
}
'''

if old_helpers not in text:
    raise SystemExit("trusted_workspace_paths block not found")
text = text.replace(old_helpers, new_helpers, 1)

old_dto = '''fn workspace_trust_dto(state_dir: &Path, workspace: &Path) -> Value {
    let canonical = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf())
        .to_string_lossy()
        .to_string();
    let commands = requested_commands(workspace);
    let trusted = trusted_workspace_paths(state_dir).contains(&canonical);
    json!({
        "workspace": canonical,
        "requested_commands": commands,
        "trusted": trusted,
        "required": !trusted && !commands.is_empty(),
        "exists": workspace.is_dir(),
    })
}
'''

new_dto = '''fn workspace_trust_dto_from_paths(workspace: &Path, trusted_paths: &[String]) -> Value {
    let canonical = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf())
        .to_string_lossy()
        .to_string();
    let commands = requested_commands(workspace);
    let trusted = trusted_paths.contains(&canonical);
    json!({
        "workspace": canonical,
        "requested_commands": commands,
        "trusted": trusted,
        "required": !trusted && !commands.is_empty(),
        "exists": workspace.is_dir(),
    })
}

fn workspace_trust_dto(
    state_dir: &Path,
    workspace: &Path,
) -> Result<Value, ShadowReadError> {
    let trusted_paths = trusted_workspace_paths(state_dir)?;
    Ok(workspace_trust_dto_from_paths(workspace, &trusted_paths))
}
'''

if old_dto not in text:
    raise SystemExit("workspace_trust_dto block not found")
text = text.replace(old_dto, new_dto, 1)

old_open = '''    let canonical_text = canonical.to_string_lossy().to_string();
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.execute(
        "INSERT INTO workspaces (path, last_used) VALUES (?1, CURRENT_TIMESTAMP)
         ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
        params![canonical_text],
    )?;
'''
new_open = '''    let canonical_text = canonical.to_string_lossy().to_string();
    let command_trust = workspace_trust_dto(state_dir, &canonical)?;
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.execute(
        "INSERT INTO workspaces (path, last_used) VALUES (?1, CURRENT_TIMESTAMP)
         ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
        params![canonical_text],
    )?;
'''
if old_open not in text:
    raise SystemExit("open_workspace authority ordering block not found")
text = text.replace(old_open, new_open, 1)

old_open_response = '''        "command_trust": workspace_trust_dto(state_dir, &canonical),
'''
new_open_response = '''        "command_trust": command_trust,
'''
if old_open_response not in text:
    raise SystemExit("open_workspace response block not found")
text = text.replace(old_open_response, new_open_response, 1)

old_list = '''pub fn list_trusted_workspaces(state_dir: &Path) -> Result<Value, ShadowReadError> {
    let workspaces = trusted_workspace_paths(state_dir)
        .into_iter()
        .map(|path| workspace_trust_dto(state_dir, Path::new(&path)))
        .collect::<Vec<_>>();
    Ok(json!({"workspaces": workspaces}))
}
'''
new_list = '''pub fn list_trusted_workspaces(state_dir: &Path) -> Result<Value, ShadowReadError> {
    let paths = trusted_workspace_paths(state_dir)?;
    let workspaces = paths
        .iter()
        .map(|path| workspace_trust_dto_from_paths(Path::new(path), &paths))
        .collect::<Vec<_>>();
    Ok(json!({"workspaces": workspaces}))
}
'''
if old_list not in text:
    raise SystemExit("list_trusted_workspaces block not found")
text = text.replace(old_list, new_list, 1)

old_set = '''    let canonical_text = canonical.to_string_lossy().to_string();
    let mut paths = trusted_workspace_paths(state_dir);
    paths.retain(|item| item != &canonical_text);
    if trusted {
        paths.push(canonical_text);
        paths.sort();
    }
    fs::create_dir_all(state_dir)?;
    fs::write(
        trust_path(state_dir),
        serde_json::to_vec_pretty(&json!({"trusted_workspaces": paths}))?,
    )?;
    let mut response = workspace_trust_dto(state_dir, &canonical);
    response["ok"] = Value::Bool(true);
    Ok(response)
'''
new_set = '''    let canonical_text = canonical.to_string_lossy().to_string();
    let mut paths = trusted_workspace_paths(state_dir)?;
    paths.retain(|item| item != &canonical_text);
    if trusted {
        paths.push(canonical_text);
        paths.sort();
    }
    fs::create_dir_all(state_dir)?;
    fs::write(
        trust_path(state_dir),
        serde_json::to_vec_pretty(&json!({"trusted_workspaces": paths}))?,
    )?;
    let mut response = workspace_trust_dto_from_paths(&canonical, &paths);
    response["ok"] = Value::Bool(true);
    Ok(response)
'''
if old_set not in text:
    raise SystemExit("set_workspace_trusted block not found")
text = text.replace(old_set, new_set, 1)

marker = '''    #[test]
    fn workspace_filter_and_recent() {
'''
if marker not in text:
    raise SystemExit("workspace test insertion marker not found")

tests = '''    #[test]
    fn workspace_trust_missing_file_is_valid_empty_state() {
        let state = tempfile::tempdir().unwrap();
        let listed = list_trusted_workspaces(state.path()).unwrap();
        assert_eq!(listed["workspaces"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn corrupt_workspace_trust_fails_closed_and_is_not_overwritten() {
        let state = tempfile::tempdir().unwrap();
        let workspace = tempfile::tempdir().unwrap();
        let authority_path = trust_path(state.path());
        fs::write(&authority_path, b"{broken").unwrap();
        let before = fs::read(&authority_path).unwrap();

        assert!(list_trusted_workspaces(state.path()).is_err());
        assert!(open_workspace(
            state.path(),
            workspace.path().to_str().unwrap(),
            false
        )
        .is_err());
        assert!(!state.path().join("core.db").exists());
        assert!(set_workspace_trusted(
            state.path(),
            workspace.path().to_str().unwrap(),
            true
        )
        .is_err());
        assert_eq!(fs::read(&authority_path).unwrap(), before);
    }

    #[test]
    fn invalid_workspace_trust_shapes_fail_closed() {
        let state = tempfile::tempdir().unwrap();
        let authority_path = trust_path(state.path());
        for invalid in [
            "[]",
            "{}",
            r#"{"trusted_workspaces":"not-an-array"}"#,
            r#"{"trusted_workspaces":[1]}"#,
        ] {
            fs::write(&authority_path, invalid).unwrap();
            assert!(list_trusted_workspaces(state.path()).is_err(), "{invalid}");
        }
    }

    #[test]
    fn workspace_trust_roundtrip_uses_single_rust_authority() {
        let state = tempfile::tempdir().unwrap();
        let workspace = tempfile::tempdir().unwrap();
        let workspace_path = workspace.path().to_str().unwrap();

        let trusted = set_workspace_trusted(state.path(), workspace_path, true).unwrap();
        assert_eq!(trusted["trusted"], true);
        let listed = list_trusted_workspaces(state.path()).unwrap();
        assert_eq!(listed["workspaces"].as_array().unwrap().len(), 1);

        let untrusted = set_workspace_trusted(state.path(), workspace_path, false).unwrap();
        assert_eq!(untrusted["trusted"], false);
        let listed = list_trusted_workspaces(state.path()).unwrap();
        assert_eq!(listed["workspaces"].as_array().unwrap().len(), 0);
    }

'''
text = text.replace(marker, tests + marker, 1)

path.write_text(text, encoding="utf-8")
