//! Rust authority for folder-backed Delta skills.
//!
//! A Skill is a controlled extension unit, not a second agent. Its `SKILL.md`
//! front-matter declares an executable contract that the Runtime / Policy /
//! Capability layer consumes; the Skill itself never runs its own scheduler,
//! approval, retry, or authority — every tool call still flows through the
//! single main Agent via `Task -> Policy -> Capability/Worker -> Validation ->
//! Artifact/Ledger`.

use std::collections::{BTreeMap, HashSet};
use std::io::{Cursor, ErrorKind, Read};
use std::path::{Component, Path, PathBuf};

use base64::Engine;
use serde_json::{json, Value};

use crate::{durability::atomic_write, ShadowReadError};

/// Declared side-effect class of a Skill's execution. This is metadata the
/// Policy layer uses to bound what a Skill may cause; it is not an execution
/// path (the main Agent still runs every tool call through Policy).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SideEffectClass {
    #[default]
    /// No side effects — pure reasoning / analysis.
    None,
    /// Read-only access (files, search, inspection).
    Read,
    /// Reversible local writes within the workspace.
    LocalWrite,
    /// External effects (messages, connectors, remote writes).
    ExternalWrite,
    /// Irreversible or destructive actions.
    Destructive,
}

impl SideEffectClass {
    fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "none" => Some(Self::None),
            "read" | "read_only" | "read-only" => Some(Self::Read),
            "local_write" | "local-write" | "write" => Some(Self::LocalWrite),
            "external_write" | "external-write" | "external" => Some(Self::ExternalWrite),
            "destructive" => Some(Self::Destructive),
            _ => None,
        }
    }

    fn as_str(&self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Read => "read",
            Self::LocalWrite => "local_write",
            Self::ExternalWrite => "external_write",
            Self::Destructive => "destructive",
        }
    }
}

/// The executable contract for a Skill, parsed from its `SKILL.md` front-matter.
///
/// Every field is optional in the file and falls back to a safe default; a
/// malformed value fails closed so a broken contract can never silently weaken
/// the Runtime / Policy boundary.
#[derive(Debug, Clone, Default)]
pub struct SkillContract {
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub source: String,
    pub version: String,
    pub compatibility: String,
    pub side_effect: SideEffectClass,
    pub capabilities: Vec<String>,
    pub permissions: Vec<String>,
    pub timeout_secs: Option<u64>,
    pub cancellable: bool,
    pub worker: Option<String>,
    pub input_schema: Option<Value>,
    pub output_schema: Option<Value>,
}

pub struct SkillStore {
    state_dir: PathBuf,
}

impl SkillStore {
    pub fn open(state_dir: impl AsRef<Path>) -> Result<Self, ShadowReadError> {
        std::fs::create_dir_all(state_dir.as_ref().join("skills"))?;
        Ok(Self {
            state_dir: state_dir.as_ref().to_path_buf(),
        })
    }

    fn global_dir(&self) -> PathBuf {
        self.state_dir.join("skills")
    }

    fn base(&self, scope: &str, workspace: Option<&str>) -> Result<PathBuf, ShadowReadError> {
        match scope {
            "global" => Ok(self.global_dir()),
            "project" => {
                let workspace = workspace
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| ShadowReadError::Parse("workspace is required".to_string()))?;
                let workspace = PathBuf::from(workspace)
                    .canonicalize()
                    .map_err(|error| ShadowReadError::Parse(error.to_string()))?;
                if !workspace.is_dir() {
                    return Err(ShadowReadError::Parse(
                        "workspace must be a directory".to_string(),
                    ));
                }
                Ok(workspace.join(".delta").join("skills"))
            }
            _ => Err(ShadowReadError::Parse("invalid skill scope".to_string())),
        }
    }

    pub fn list(&self, workspace: Option<&str>) -> Result<Vec<Value>, ShadowReadError> {
        let disabled = self.disabled()?;
        let mut rows = BTreeMap::new();
        let mut scopes = vec![(self.global_dir(), "global")];
        if let Some(workspace) = workspace.filter(|value| !value.is_empty()) {
            scopes.push((self.base("project", Some(workspace))?, "project"));
        }
        for (base, scope) in scopes {
            let Ok(entries) = std::fs::read_dir(&base) else {
                continue;
            };
            for entry in entries.flatten() {
                let folder = entry.path();
                if folder.is_symlink() || !folder.is_dir() {
                    continue;
                }
                let skill_md = folder.join("SKILL.md");
                if !skill_md.exists() {
                    continue;
                }
                let text = std::fs::read_to_string(&skill_md)?;
                let fallback = folder
                    .file_name()
                    .map(|value| value.to_string_lossy().to_string())
                    .unwrap_or_default();
                let contract = parse_skill(&text, &fallback)?.contract;
                let files = walk_files(&folder)?.saturating_sub(1);
                rows.insert(
                    contract.name.clone(),
                    json!({
                        "name": contract.name,
                        "description": contract.description,
                        "instructions": contract.instructions,
                        "scope": scope,
                        "source": contract.source,
                        "enabled": !disabled.contains(&contract.name),
                        "path": folder,
                        "files": files,
                        "version": contract.version,
                        "compatibility": contract.compatibility,
                        "side_effect": contract.side_effect.as_str(),
                        "capabilities": contract.capabilities,
                        "permissions": contract.permissions,
                        "timeout_secs": contract.timeout_secs,
                        "cancellable": contract.cancellable,
                        "worker": contract.worker,
                        "input_schema": contract.input_schema,
                        "output_schema": contract.output_schema,
                    }),
                );
            }
        }
        Ok(rows.into_values().collect())
    }

    pub fn create(&self, body: &Value) -> Result<Value, ShadowReadError> {
        let name = validate_name(body.get("name").and_then(Value::as_str).unwrap_or_default())?;
        let instructions = body
            .get("instructions")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        if instructions.is_empty() {
            return Ok(json!({"ok": false, "error": "skill instructions are required"}));
        }
        let scope = body
            .get("scope")
            .and_then(Value::as_str)
            .unwrap_or("global");
        let base = self.base(scope, body.get("workspace").and_then(Value::as_str))?;
        let folder = base.join(&name);
        if folder.exists() {
            return Ok(json!({"ok": false, "error": "skill already exists"}));
        }
        std::fs::create_dir_all(&folder)?;
        write_skill(
            &folder,
            &name,
            body.get("description")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            instructions,
            "local",
        )?;
        Ok(json!({"ok": true, "name": name, "scope": scope}))
    }

    pub fn update(
        &self,
        name: &str,
        patch: &Value,
        workspace: Option<&str>,
    ) -> Result<Value, ShadowReadError> {
        let name = validate_name(name)?;
        if let Some(enabled) = patch.get("enabled").and_then(Value::as_bool) {
            self.set_enabled(&name, enabled)?;
        }
        if patch.get("description").is_some() || patch.get("instructions").is_some() {
            let (folder, _) = self.find(&name, workspace)?;
            let current =
                parse_skill(&std::fs::read_to_string(folder.join("SKILL.md"))?, &name)?.contract;
            let instructions = patch
                .get("instructions")
                .and_then(Value::as_str)
                .unwrap_or(&current.instructions);
            if instructions.trim().is_empty() {
                return Ok(json!({"ok": false, "error": "skill instructions are required"}));
            }
            write_skill(
                &folder,
                &name,
                patch
                    .get("description")
                    .and_then(Value::as_str)
                    .unwrap_or(&current.description),
                instructions,
                &current.source,
            )?;
        }
        Ok(json!({"ok": true}))
    }

    pub fn delete(&self, name: &str, workspace: Option<&str>) -> Result<Value, ShadowReadError> {
        let (folder, _) = self.find(&validate_name(name)?, workspace)?;
        std::fs::remove_dir_all(folder)?;
        Ok(json!({"ok": true}))
    }

    pub fn move_skill(
        &self,
        name: &str,
        scope: &str,
        workspace: Option<&str>,
    ) -> Result<Value, ShadowReadError> {
        let name = validate_name(name)?;
        let (source, current_scope) = self.find(&name, workspace)?;
        if current_scope == scope {
            return Ok(json!({"ok": true, "scope": scope}));
        }
        let destination = self.base(scope, workspace)?.join(&name);
        if destination.exists() {
            return Ok(json!({"ok": false, "error": "skill exists in target scope"}));
        }
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::rename(source, destination)?;
        Ok(json!({"ok": true, "scope": scope}))
    }

    pub fn resolve_folder(
        &self,
        name: &str,
        workspace: Option<&str>,
    ) -> Result<PathBuf, ShadowReadError> {
        self.find(&validate_name(name)?, workspace)
            .map(|(folder, _)| folder)
    }

    pub fn stage_upload(&self, encoded: &str, filename: &str) -> Result<Value, ShadowReadError> {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .map_err(|error| ShadowReadError::Parse(error.to_string()))?;
        if bytes.len() > 25 * 1024 * 1024 {
            return Err(ShadowReadError::Parse(
                "skill upload exceeds the 25 MiB limit".to_string(),
            ));
        }
        let token = uuid::Uuid::new_v4().simple().to_string();
        let staged = self.state_dir.join("skills-staged").join(&token);
        std::fs::create_dir_all(&staged)?;
        if filename.to_ascii_lowercase().ends_with(".zip") {
            extract_zip(&bytes, &staged)?;
        } else {
            std::fs::write(staged.join("SKILL.md"), bytes)?;
        }
        let skill_path = find_skill_md(&staged)?;
        if skill_path.parent() != Some(staged.as_path()) {
            let root = skill_path.parent().unwrap();
            for entry in std::fs::read_dir(root)?.flatten() {
                std::fs::rename(entry.path(), staged.join(entry.file_name()))?;
            }
        }
        let parsed = parse_skill(&std::fs::read_to_string(staged.join("SKILL.md"))?, "")?.contract;
        let name = validate_name(&parsed.name)?;
        let files = list_relative_files(&staged)?;
        Ok(json!({
            "ok": true, "token": token, "name": name,
            "description": parsed.description, "instructions": parsed.instructions,
            "files": files.into_iter().filter(|path| path != "SKILL.md").collect::<Vec<_>>(),
        }))
    }

    pub fn confirm_upload(
        &self,
        token: &str,
        scope: &str,
        workspace: Option<&str>,
    ) -> Result<Value, ShadowReadError> {
        let token = validate_name(token)?;
        let staged = self.state_dir.join("skills-staged").join(token);
        let parsed = parse_skill(&std::fs::read_to_string(staged.join("SKILL.md"))?, "")?.contract;
        let name = validate_name(&parsed.name)?;
        let destination = self.base(scope, workspace)?.join(&name);
        if destination.exists() {
            return Ok(json!({"ok": false, "error": "skill already exists"}));
        }
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::rename(staged, destination)?;
        Ok(json!({"ok": true, "name": name, "scope": scope}))
    }

    pub fn session_rows(
        &self,
        session_id: &str,
        workspace: Option<&str>,
    ) -> Result<Vec<Value>, ShadowReadError> {
        let overrides = self.session_overrides()?;
        let session = overrides.get(session_id).and_then(Value::as_object);
        Ok(self
            .list(workspace)?
            .into_iter()
            .map(|row| {
                let name = row["name"].as_str().unwrap_or_default();
                json!({
                    "name": name,
                    "description": row["description"],
                    "scope": row["scope"],
                    "side_effect": row["side_effect"],
                    "enabled": session.and_then(|value| value.get(name)).and_then(Value::as_bool).unwrap_or_else(|| row["enabled"].as_bool().unwrap_or(true)),
                })
            })
            .collect())
    }

    pub fn set_session(
        &self,
        session_id: &str,
        skill: &str,
        enabled: bool,
        clear: bool,
        workspace: Option<&str>,
    ) -> Result<Value, ShadowReadError> {
        validate_name(skill)?;
        let mut overrides = self.session_overrides()?;
        let session = overrides
            .entry(session_id.to_string())
            .or_insert_with(|| json!({}));
        let session = session.as_object_mut().unwrap();
        if clear {
            session.remove(skill);
        } else {
            session.insert(skill.to_string(), Value::Bool(enabled));
        }
        let bytes = serde_json::to_vec_pretty(&overrides)?;
        atomic_write(self.state_dir.join("session-skills.json"), &bytes)?;
        Ok(json!({"ok": true, "skills": self.session_rows(session_id, workspace)?}))
    }

    fn find(
        &self,
        name: &str,
        workspace: Option<&str>,
    ) -> Result<(PathBuf, &'static str), ShadowReadError> {
        if let Some(workspace) = workspace.filter(|value| !value.is_empty()) {
            let project = self.base("project", Some(workspace))?.join(name);
            if project.join("SKILL.md").is_file() && !project.is_symlink() {
                return Ok((project, "project"));
            }
        }
        let global = self.global_dir().join(name);
        if global.join("SKILL.md").is_file() && !global.is_symlink() {
            return Ok((global, "global"));
        }
        Err(ShadowReadError::Parse("skill not found".to_string()))
    }

    fn disabled(&self) -> Result<HashSet<String>, ShadowReadError> {
        let path = self.state_dir.join("skills-settings.json");
        let Some(value) = read_optional_json(&path)? else {
            return Ok(HashSet::new());
        };
        let root = value.as_object().ok_or_else(|| {
            ShadowReadError::Parse("skill settings root must be an object".to_string())
        })?;
        let disabled = root
            .get("disabled")
            .ok_or_else(|| {
                ShadowReadError::Parse("skill settings are missing disabled".to_string())
            })?
            .as_array()
            .ok_or_else(|| {
                ShadowReadError::Parse("skill settings disabled must be an array".to_string())
            })?;
        let mut names = HashSet::new();
        for value in disabled {
            let name = value.as_str().ok_or_else(|| {
                ShadowReadError::Parse(
                    "skill settings disabled entries must be strings".to_string(),
                )
            })?;
            names.insert(validate_name(name)?);
        }
        Ok(names)
    }

    fn set_enabled(&self, name: &str, enabled: bool) -> Result<(), ShadowReadError> {
        let mut disabled = self.disabled()?;
        if enabled {
            disabled.remove(name);
        } else {
            disabled.insert(name.to_string());
        }
        let mut disabled = disabled.into_iter().collect::<Vec<_>>();
        disabled.sort();
        let bytes = serde_json::to_vec_pretty(&json!({"disabled": disabled}))?;
        atomic_write(self.state_dir.join("skills-settings.json"), &bytes)?;
        Ok(())
    }

    fn session_overrides(&self) -> Result<serde_json::Map<String, Value>, ShadowReadError> {
        let path = self.state_dir.join("session-skills.json");
        let Some(value) = read_optional_json(&path)? else {
            return Ok(serde_json::Map::new());
        };
        let overrides = value.as_object().cloned().ok_or_else(|| {
            ShadowReadError::Parse("session skill state root must be an object".to_string())
        })?;
        for (session_id, skills) in &overrides {
            if session_id.trim().is_empty() {
                return Err(ShadowReadError::Parse(
                    "session skill state contains an empty session id".to_string(),
                ));
            }
            let skills = skills.as_object().ok_or_else(|| {
                ShadowReadError::Parse("session skill state entries must be objects".to_string())
            })?;
            for (skill, enabled) in skills {
                validate_name(skill)?;
                if !enabled.is_boolean() {
                    return Err(ShadowReadError::Parse(
                        "session skill state flags must be booleans".to_string(),
                    ));
                }
            }
        }
        Ok(overrides)
    }
}

fn read_optional_json(path: &Path) -> Result<Option<Value>, ShadowReadError> {
    match std::fs::read_to_string(path) {
        Ok(text) => Ok(Some(serde_json::from_str::<Value>(&text)?)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

struct ParsedSkill {
    contract: SkillContract,
}

fn parse_skill(text: &str, fallback: &str) -> Result<ParsedSkill, ShadowReadError> {
    Ok(ParsedSkill {
        contract: parse_contract(text, fallback)?,
    })
}

/// Parse and validate a Skill contract from `SKILL.md` text.
///
/// A malformed contract (unknown side-effect class, non-numeric timeout,
/// invalid JSON schema, bad worker binding) fails closed — the Skill is not
/// surfaced to the Runtime / Policy layer.
fn parse_contract(text: &str, fallback: &str) -> Result<SkillContract, ShadowReadError> {
    let mut contract = SkillContract {
        name: fallback.to_string(),
        cancellable: true,
        ..SkillContract::default()
    };
    contract.instructions = text.trim().to_string();

    if let Some(rest) = text.strip_prefix("---") {
        if let Some(end) = rest.find("\n---") {
            for line in rest[..end].lines() {
                let Some((key, value)) = line.split_once(':') else {
                    continue;
                };
                let key = key.trim();
                let value = value.trim();
                if value.is_empty() {
                    continue;
                }
                match key {
                    "name" => contract.name = value.to_string(),
                    "description" => contract.description = value.to_string(),
                    "source" => contract.source = value.to_string(),
                    "version" => contract.version = value.to_string(),
                    "compatibility" => contract.compatibility = value.to_string(),
                    "side_effect" => {
                        contract.side_effect = SideEffectClass::parse(value).ok_or_else(|| {
                            ShadowReadError::Parse(format!(
                                "skill declares an unknown side_effect class: {value}"
                            ))
                        })?;
                    }
                    "capabilities" => contract.capabilities = parse_string_list(value)?,
                    "permissions" => contract.permissions = parse_string_list(value)?,
                    "timeout_secs" => {
                        contract.timeout_secs = Some(value.parse::<u64>().map_err(|_| {
                            ShadowReadError::Parse(format!(
                                "skill timeout_secs must be a non-negative integer: {value}"
                            ))
                        })?);
                    }
                    "cancellable" => {
                        contract.cancellable = parse_bool(value).ok_or_else(|| {
                            ShadowReadError::Parse(format!(
                                "skill cancellable must be true or false: {value}"
                            ))
                        })?;
                    }
                    "worker" => {
                        validate_name(value)?;
                        contract.worker = Some(value.to_string());
                    }
                    "input_schema" => contract.input_schema = Some(parse_json_value(value)?),
                    "output_schema" => contract.output_schema = Some(parse_json_value(value)?),
                    _ => {}
                }
            }
            contract.instructions = rest[end + 4..].trim().to_string();
        }
    }
    Ok(contract)
}

/// Parse a string list that is either a JSON array or a comma-separated list.
fn parse_string_list(value: &str) -> Result<Vec<String>, ShadowReadError> {
    let trimmed = value.trim();
    if trimmed.starts_with('[') {
        let parsed: Vec<String> = serde_json::from_str(trimmed).map_err(|_| {
            ShadowReadError::Parse(format!("skill field must be a JSON string array: {value}"))
        })?;
        return Ok(parsed);
    }
    Ok(trimmed
        .split(',')
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect())
}

fn parse_bool(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    }
}

/// Parse a single-line JSON value (for input/output schemas).
fn parse_json_value(value: &str) -> Result<Value, ShadowReadError> {
    serde_json::from_str(value.trim()).map_err(|_| {
        ShadowReadError::Parse(format!("skill schema field is not valid JSON: {value}"))
    })
}

fn validate_name(name: &str) -> Result<String, ShadowReadError> {
    let name = name.trim();
    if name.is_empty()
        || name.len() > 64
        || name.contains("..")
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
    {
        return Err(ShadowReadError::Parse("invalid skill name".to_string()));
    }
    Ok(name.to_string())
}

fn write_skill(
    folder: &Path,
    name: &str,
    description: &str,
    instructions: &str,
    source: &str,
) -> Result<(), ShadowReadError> {
    let text = format!(
        "---\nname: {name}\ndescription: {}\nsource: {source}\n---\n\n{}\n",
        description.replace(['\r', '\n'], " "),
        instructions.trim()
    );
    atomic_write(folder.join("SKILL.md"), text.as_bytes())?;
    Ok(())
}

fn extract_zip(bytes: &[u8], destination: &Path) -> Result<(), ShadowReadError> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|error| ShadowReadError::Parse(error.to_string()))?;
    if archive.len() > 500 {
        return Err(ShadowReadError::Parse(
            "skill archive contains too many entries".to_string(),
        ));
    }
    let mut extracted_size = 0_u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| ShadowReadError::Parse(error.to_string()))?;
        let Some(path) = entry.enclosed_name() else {
            return Err(ShadowReadError::Parse(
                "unsafe skill archive path".to_string(),
            ));
        };
        if path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        }) {
            return Err(ShadowReadError::Parse(
                "unsafe skill archive path".to_string(),
            ));
        }
        let output = destination.join(path);
        if entry.is_dir() {
            std::fs::create_dir_all(output)?;
            continue;
        }
        extracted_size = extracted_size.saturating_add(entry.size());
        if extracted_size > 100 * 1024 * 1024 {
            return Err(ShadowReadError::Parse(
                "skill archive exceeds the 100 MiB extracted limit".to_string(),
            ));
        }
        if let Some(parent) = output.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes)?;
        std::fs::write(output, bytes)?;
    }
    Ok(())
}

fn find_skill_md(root: &Path) -> Result<PathBuf, ShadowReadError> {
    let candidates = list_files(root)?
        .into_iter()
        .filter(|path| path.file_name().is_some_and(|name| name == "SKILL.md"))
        .collect::<Vec<_>>();
    if candidates.len() != 1 {
        return Err(ShadowReadError::Parse(
            "upload must contain exactly one SKILL.md".to_string(),
        ));
    }
    Ok(candidates[0].clone())
}

fn list_files(root: &Path) -> Result<Vec<PathBuf>, ShadowReadError> {
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(directory)?.flatten() {
            let path = entry.path();
            if path.is_symlink() {
                continue;
            }
            if path.is_dir() {
                pending.push(path);
            } else {
                files.push(path);
            }
        }
    }
    Ok(files)
}

fn walk_files(root: &Path) -> Result<usize, ShadowReadError> {
    Ok(list_files(root)?.len())
}

fn list_relative_files(root: &Path) -> Result<Vec<String>, ShadowReadError> {
    Ok(list_files(root)?
        .into_iter()
        .filter_map(|path| {
            path.strip_prefix(root)
                .ok()
                .map(|path| path.to_string_lossy().replace('\\', "/"))
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_crud_and_session_override_are_rust_owned() {
        let temp = tempfile::tempdir().unwrap();
        let store = SkillStore::open(temp.path()).unwrap();
        assert_eq!(
            store
                .create(&json!({"name": "brief", "description": "d", "instructions": "Do it"}))
                .unwrap()["ok"],
            true
        );
        assert_eq!(store.list(None).unwrap()[0]["name"], "brief");
        assert_eq!(
            store
                .set_session("s1", "brief", false, false, None)
                .unwrap()["skills"][0]["enabled"],
            false
        );
        assert_eq!(store.delete("brief", None).unwrap()["ok"], true);
    }

    #[test]
    fn corrupt_skill_settings_fail_closed_without_overwrite() {
        let temp = tempfile::tempdir().unwrap();
        let store = SkillStore::open(temp.path()).unwrap();
        store
            .create(&json!({"name": "brief", "instructions": "Do it"}))
            .unwrap();
        let path = temp.path().join("skills-settings.json");
        std::fs::write(&path, b"{not-json").unwrap();

        assert!(matches!(store.list(None), Err(ShadowReadError::Json(_))));
        assert!(matches!(
            store.update("brief", &json!({"enabled": false}), None),
            Err(ShadowReadError::Json(_))
        ));
        assert_eq!(std::fs::read(&path).unwrap(), b"{not-json");
    }

    #[test]
    fn invalid_skill_settings_shape_fails_closed() {
        let temp = tempfile::tempdir().unwrap();
        let store = SkillStore::open(temp.path()).unwrap();
        std::fs::write(
            temp.path().join("skills-settings.json"),
            serde_json::to_vec(&json!({"disabled": "brief"})).unwrap(),
        )
        .unwrap();
        assert!(matches!(store.list(None), Err(ShadowReadError::Parse(_))));
    }

    #[test]
    fn corrupt_session_skill_state_fails_closed_without_overwrite() {
        let temp = tempfile::tempdir().unwrap();
        let store = SkillStore::open(temp.path()).unwrap();
        store
            .create(&json!({"name": "brief", "instructions": "Do it"}))
            .unwrap();
        let path = temp.path().join("session-skills.json");
        std::fs::write(&path, b"{not-json").unwrap();

        assert!(matches!(
            store.session_rows("s1", None),
            Err(ShadowReadError::Json(_))
        ));
        assert!(matches!(
            store.set_session("s1", "brief", false, false, None),
            Err(ShadowReadError::Json(_))
        ));
        assert_eq!(std::fs::read(&path).unwrap(), b"{not-json");
    }

    #[test]
    fn invalid_session_skill_shape_fails_closed() {
        let temp = tempfile::tempdir().unwrap();
        let store = SkillStore::open(temp.path()).unwrap();
        std::fs::write(
            temp.path().join("session-skills.json"),
            serde_json::to_vec(&json!({"s1": {"brief": "disabled"}})).unwrap(),
        )
        .unwrap();
        assert!(matches!(
            store.session_rows("s1", None),
            Err(ShadowReadError::Parse(_))
        ));
    }

    // ------------------------------------------------------------------
    // R7 Task 5: Skill executable contract.
    // ------------------------------------------------------------------

    #[test]
    fn skill_contract_parses_full_front_matter() {
        let contract = parse_contract(
            "---\nname: brief\nversion: 1.2.0\ncompatibility: delta>=1.0\nside_effect: local_write\ncapabilities: file.read,file.write\npermissions: workspace.read\nworkspace.write\ntimeout_secs: 30\ncancellable: false\nworker: office\ninput_schema: {\"type\":\"object\"}\n---\nDo the summary.",
            "fallback",
        )
        .unwrap();
        assert_eq!(contract.name, "brief");
        assert_eq!(contract.version, "1.2.0");
        assert_eq!(contract.compatibility, "delta>=1.0");
        assert_eq!(contract.side_effect, SideEffectClass::LocalWrite);
        assert_eq!(contract.capabilities, vec!["file.read", "file.write"]);
        assert_eq!(contract.timeout_secs, Some(30));
        assert!(!contract.cancellable);
        assert_eq!(contract.worker.as_deref(), Some("office"));
        assert_eq!(contract.input_schema, Some(json!({"type": "object"})));
        assert_eq!(contract.instructions, "Do the summary.");
    }

    #[test]
    fn skill_contract_defaults_are_safe_when_fields_absent() {
        let contract = parse_contract("Do it", "fallback").unwrap();
        assert_eq!(contract.name, "fallback");
        assert_eq!(contract.side_effect, SideEffectClass::None);
        assert!(contract.capabilities.is_empty());
        assert!(contract.permissions.is_empty());
        assert_eq!(contract.timeout_secs, None);
        assert!(contract.cancellable);
        assert_eq!(contract.worker, None);
        assert_eq!(contract.input_schema, None);
    }

    #[test]
    fn skill_contract_unknown_side_effect_fails_closed() {
        assert!(matches!(
            parse_contract("---\nname: bad\nside_effect: explodes\n---\nDo it", "bad"),
            Err(ShadowReadError::Parse(_))
        ));
    }

    #[test]
    fn skill_contract_non_numeric_timeout_fails_closed() {
        assert!(matches!(
            parse_contract("---\nname: bad\ntimeout_secs: soon\n---\nDo it", "bad"),
            Err(ShadowReadError::Parse(_))
        ));
    }

    #[test]
    fn skill_contract_invalid_schema_fails_closed() {
        assert!(matches!(
            parse_contract("---\nname: bad\ninput_schema: {not json\n---\nDo it", "bad"),
            Err(ShadowReadError::Parse(_))
        ));
    }

    #[test]
    fn skill_contract_unknown_worker_name_fails_closed() {
        assert!(matches!(
            parse_contract("---\nname: bad\nworker: ../evil\n---\nDo it", "bad"),
            Err(ShadowReadError::Parse(_))
        ));
    }

    #[test]
    fn skill_contract_json_array_capabilities() {
        let contract = parse_contract(
            "---\nname: brief\ncapabilities: [\"file.read\",\"search\"]\n---\nDo it",
            "fallback",
        )
        .unwrap();
        assert_eq!(contract.capabilities, vec!["file.read", "search"]);
    }

    #[test]
    fn skill_list_exposes_contract_and_fails_closed_on_bad_contract() {
        let temp = tempfile::tempdir().unwrap();
        let store = SkillStore::open(temp.path()).unwrap();
        store
            .create(&json!({"name": "brief", "description": "d", "instructions": "Do it"}))
            .unwrap();
        // Overwrite with a full contract.
        std::fs::write(
            temp.path().join("skills").join("brief").join("SKILL.md"),
            "---\nname: brief\ndescription: d\nsource: local\nside_effect: external_write\ntimeout_secs: 10\n---\nDo it",
        )
        .unwrap();
        let rows = store.list(None).unwrap();
        assert_eq!(rows[0]["side_effect"], "external_write");
        assert_eq!(rows[0]["timeout_secs"], 10);

        // A corrupt contract must fail closed, not be silently skipped.
        std::fs::write(
            temp.path().join("skills").join("brief").join("SKILL.md"),
            "---\nname: brief\nside_effect: bogus\n---\nDo it",
        )
        .unwrap();
        assert!(matches!(store.list(None), Err(ShadowReadError::Parse(_))));
    }
}
