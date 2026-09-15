from pathlib import Path

path = Path("core/runtime-native/src/control_plane.rs")
text = path.read_text(encoding="utf-8")

old_helper = '''fn workspace_trust_dto(state_dir: &Path, workspace: &Path) -> Result<Value, ShadowReadError> {
    let trusted_paths = trusted_workspace_paths(state_dir)?;
    Ok(workspace_trust_dto_from_paths(workspace, &trusted_paths))
}

'''
if old_helper not in text:
    raise SystemExit("workspace_trust_dto helper not found")
text = text.replace(old_helper, "", 1)

old_open_start = '''pub fn open_workspace(
    state_dir: &Path,
    path: &str,
    create: bool,
) -> Result<Value, ShadowReadError> {
    let requested = PathBuf::from(path);
'''
new_open_start = '''pub fn open_workspace(
    state_dir: &Path,
    path: &str,
    create: bool,
) -> Result<Value, ShadowReadError> {
    let trusted_paths = trusted_workspace_paths(state_dir)?;
    let requested = PathBuf::from(path);
'''
if old_open_start not in text:
    raise SystemExit("open_workspace start not found")
text = text.replace(old_open_start, new_open_start, 1)

old_command_trust = '''    let command_trust = workspace_trust_dto(state_dir, &canonical)?;
'''
new_command_trust = '''    let command_trust = workspace_trust_dto_from_paths(&canonical, &trusted_paths);
'''
if old_command_trust not in text:
    raise SystemExit("command trust line not found")
text = text.replace(old_command_trust, new_command_trust, 1)

old_test = '''        assert!(list_trusted_workspaces(state.path()).is_err());
        assert!(open_workspace(state.path(), workspace.path().to_str().unwrap(), false).is_err());
        assert!(!state.path().join("core.db").exists());
'''
new_test = '''        assert!(list_trusted_workspaces(state.path()).is_err());
        let uncreated = state.path().join("must-not-be-created");
        assert!(open_workspace(state.path(), uncreated.to_str().unwrap(), true).is_err());
        assert!(!uncreated.exists());
        assert!(open_workspace(state.path(), workspace.path().to_str().unwrap(), false).is_err());
        assert!(!state.path().join("core.db").exists());
'''
if old_test not in text:
    raise SystemExit("corruption ordering test block not found")
text = text.replace(old_test, new_test, 1)

path.write_text(text, encoding="utf-8")
