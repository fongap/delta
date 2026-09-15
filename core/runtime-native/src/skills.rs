//! Rust authority for folder-backed Delta skills.

use std::collections::{BTreeMap, HashSet};
use std::io::{Cursor, ErrorKind, Read};
use std::path::{Component, Path, PathBuf};

use base64::Engine;
use serde_json::{json, Value};

use crate::ShadowReadError;

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
                let Ok(text) = std::fs::read_to_string(&skill_md) else {
                    continue;
                };
                let fallback = folder
                    .file_name()
                    .map(|value| value.to_string_lossy().to_string())
                    .unwrap_or_default();
                let parsed = parse_skill(&text, &fallback);
                let files = walk_files(&folder)?.saturating_sub(1);
                rows.insert(
                    parsed.name.clone(),
                    json!({
                        "name": parsed.name,
                        "description": parsed.description,
                        "instructions": parsed.instructions,
                        "scope": scope,
                        "source": parsed.source,
                        "enabled": !disabled.contains(&parsed.name),
                        "path": folder,
                        "files": files,
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
            let current = parse_skill(&std::fs::read_to_string(folder.join("SKILL.md"))?, &name);
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
        let parsed = parse_skill(&std::fs::read_to_string(staged.join("SKILL.md"))?, "");
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
        let parsed = parse_skill(&std::fs::read_to_string(staged.join("SKILL.md"))?, "");
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
        std::fs::write(
            self.state_dir.join("session-skills.json"),
            serde_json::to_vec_pretty(&overrides)?,
        )?;
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
        std::fs::write(
            self.state_dir.join("skills-settings.json"),
            serde_json::to_vec_pretty(&json!({"disabled": disabled}))?,
        )?;
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
    name: String,
    description: String,
    instructions: String,
    source: String,
}

fn parse_skill(text: &str, fallback: &str) -> ParsedSkill {
    let mut name = fallback.to_string();
    let mut description = String::new();
    let mut source = "local".to_string();
    let mut instructions = text.trim().to_string();
    if let Some(rest) = text.strip_prefix("---") {
        if let Some(end) = rest.find("\n---") {
            for line in rest[..end].lines() {
                if let Some((key, value)) = line.split_once(':') {
                    match key.trim() {
                        "name" => name = value.trim().to_string(),
                        "description" => description = value.trim().to_string(),
                        "source" => source = value.trim().to_string(),
                        _ => {}
                    }
                }
            }
            instructions = rest[end + 4..].trim().to_string();
        }
    }
    ParsedSkill {
        name,
        description,
        instructions,
        source,
    }
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
    std::fs::write(folder.join("SKILL.md"), text)?;
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
}
