//! R6 Application Control Plane — Rust authority for Session/Workspace state.
//!
//! Replaces the Python `SessionManager` / `ConversationStore` control plane. Reads
//! the persisted Delta state directly (SQLite `core.db` sessions index +
//! `conversations/<id>.jsonl` message logs), so existing user data keeps working.
//!
//! Ownership: the Rust core is the sole authority for session metadata and
//! workspace trust state. Writes go through this module (no Python facade).

use std::fs;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

use base64::Engine;
use rusqlite::{params, Connection};
use serde_json::{json, Value};

use crate::ShadowReadError;

const SESSIONS_TABLE: &str = "CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, mode TEXT,
    title TEXT, agent TEXT DEFAULT 'delta', n_msgs INTEGER DEFAULT 0, messages TEXT,
    extra_roots TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
    origin TEXT, origin_label TEXT,
    auto_title TEXT, renamed INTEGER DEFAULT 0,
    grants TEXT, compaction TEXT, reasoning_effort TEXT DEFAULT 'auto',
    recovery TEXT,
    migration_marker TEXT, unattended INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);";

const WORKSPACES_TABLE: &str = "CREATE TABLE IF NOT EXISTS workspaces (
    path TEXT PRIMARY KEY, last_used TEXT DEFAULT CURRENT_TIMESTAMP
);";

/// Start a read-write SQLite connection to `core.db`, ensuring the schema.
fn open_conn(db_path: &Path) -> Result<Connection, ShadowReadError> {
    if let Some(parent) = db_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let conn = Connection::open(db_path)?;
    conn.prepare(SESSIONS_TABLE)?.execute(params![])?;
    conn.prepare(WORKSPACES_TABLE)?.execute(params![])?;
    for (name, ddl) in [
        ("grants", "ALTER TABLE sessions ADD COLUMN grants TEXT"),
        (
            "compaction",
            "ALTER TABLE sessions ADD COLUMN compaction TEXT",
        ),
        (
            "reasoning_effort",
            "ALTER TABLE sessions ADD COLUMN reasoning_effort TEXT DEFAULT 'auto'",
        ),
        (
            "migration_marker",
            "ALTER TABLE sessions ADD COLUMN migration_marker TEXT",
        ),
        (
            "unattended",
            "ALTER TABLE sessions ADD COLUMN unattended INTEGER DEFAULT 0",
        ),
    ] {
        let present = conn.query_row(
            "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name = ?1",
            params![name],
            |row| row.get::<_, i64>(0),
        )? > 0;
        if !present {
            conn.execute(ddl, params![])?;
        }
    }
    migrate_legacy_session_rows(&conn)?;
    Ok(conn)
}

/// One-time compatibility migration for sessions created by the retired
/// multi-agent product. Only the binding and marker change; transcript,
/// workspace, model, title, and user flags remain byte-for-byte untouched.
fn migrate_legacy_session_rows(conn: &Connection) -> Result<usize, rusqlite::Error> {
    conn.execute(
        "UPDATE sessions
         SET migration_marker = printf(
               'r6-agent-migration:%s->delta',
               COALESCE(NULLIF(TRIM(agent), ''), '<unset>')
             ),
             agent = 'delta'
         WHERE COALESCE(TRIM(agent), '') <> 'delta'",
        params![],
    )
}

/// The conversations dir where `<session_id>.jsonl` lives.
fn conversations_dir(state_dir: &Path) -> PathBuf {
    state_dir.join("conversations")
}

/// Load messages from `<state_dir>/conversations/<id>.jsonl` (append-only log).
fn load_messages(state_dir: &Path, session_id: &str) -> Vec<Value> {
    if !safe_session_id(session_id) {
        return Vec::new();
    }
    let path = conversations_dir(state_dir).join(format!("{session_id}.jsonl"));
    let Ok(file) = fs::File::open(&path) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for line in std::io::BufReader::new(file).lines().map_while(Result::ok) {
        if let Ok(v) = serde_json::from_str::<Value>(&line) {
            out.push(v);
        }
    }
    out
}

/// Serialize one session row into the frontend `SessionDto` shape.
fn row_to_dto(row: &rusqlite::Row) -> rusqlite::Result<Value> {
    let updated_at: Option<String> = row.get("updated_at")?;
    let origin: Option<String> = row.get("origin")?;
    Ok(json!({
        "session_id": row.get::<_, String>("session_id")?,
        "title": row.get::<_, Option<String>>("title")?,
        "workspace": row.get::<_, Option<String>>("workspace")?.unwrap_or_default(),
        "agent": row.get::<_, Option<String>>("agent")?.unwrap_or_else(|| "delta".to_string()),
        "model": row.get::<_, Option<String>>("model")?.unwrap_or_default(),
        "mode": row.get::<_, Option<String>>("mode")?.unwrap_or_else(|| "interactive".to_string()),
        "updated_at": updated_at,
        "messages": row.get::<_, i64>("n_msgs")?,
        "pinned": row.get::<_, i64>("pinned")? != 0,
        "archived": row.get::<_, i64>("archived")? != 0,
        "origin": origin,
        "reasoning_effort": row.get::<_, Option<String>>("reasoning_effort")?.unwrap_or_else(|| "auto".to_string()),
    }))
}

fn safe_session_id(session_id: &str) -> bool {
    !session_id.is_empty()
        && session_id.len() <= 128
        && session_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

/// Ensure the Rust-owned session row exists before an active Runtime is
/// registered. Existing transcript/title fields are preserved.
pub fn ensure_session(
    state_dir: &Path,
    session_id: &str,
    workspace: Option<&str>,
    model: &str,
) -> Result<Value, ShadowReadError> {
    if !safe_session_id(session_id) {
        return Err(ShadowReadError::Parse("unsafe session id".to_string()));
    }
    let workspace = workspace.unwrap_or_default();
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.execute(
        "INSERT INTO sessions
         (session_id, workspace, model, mode, title, agent, n_msgs, reasoning_effort, migration_marker, updated_at)
         VALUES (?1, ?2, ?3, 'interactive', NULL, 'delta', 0, 'auto', 'r6-rust-authority', CURRENT_TIMESTAMP)
         ON CONFLICT(session_id) DO UPDATE SET
           workspace = CASE WHEN excluded.workspace = '' THEN sessions.workspace ELSE excluded.workspace END,
           model = excluded.model, agent = 'delta',
           migration_marker = CASE
             WHEN sessions.migration_marker LIKE 'r6-agent-migration:%'
             THEN sessions.migration_marker
             ELSE 'r6-rust-authority'
           END,
           updated_at = CURRENT_TIMESTAMP",
        params![session_id, workspace, model],
    )?;
    if !workspace.is_empty() {
        conn.execute(
            "INSERT INTO workspaces (path, last_used) VALUES (?1, CURRENT_TIMESTAMP)
             ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
            params![workspace],
        )?;
    }
    fs::create_dir_all(conversations_dir(state_dir))?;
    Ok(json!({"ok": true, "session_id": session_id}))
}

/// Append one canonical transcript message and keep the SQLite index count in
/// lockstep. Runtime events call this; React never writes transcript state.
pub fn append_session_message(
    state_dir: &Path,
    session_id: &str,
    message: &Value,
) -> Result<(), ShadowReadError> {
    if !safe_session_id(session_id) {
        return Err(ShadowReadError::Parse("unsafe session id".to_string()));
    }
    fs::create_dir_all(conversations_dir(state_dir))?;
    let path = conversations_dir(state_dir).join(format!("{session_id}.jsonl"));
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    serde_json::to_writer(&mut file, message)?;
    file.write_all(b"\n")?;
    file.flush()?;
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.execute(
        "UPDATE sessions SET n_msgs = n_msgs + 1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?1",
        params![session_id],
    )?;
    Ok(())
}

/// Persist transcript and recovery state from the single RuntimeEvent protocol.
pub fn record_runtime_event(state_dir: &Path, frame: &Value) -> Result<(), ShadowReadError> {
    let session_id = frame
        .get("sessionId")
        .and_then(Value::as_str)
        .ok_or_else(|| ShadowReadError::Parse("runtime event missing session id".to_string()))?;
    let event_type = frame
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let payload = frame.get("payload").cloned().unwrap_or_else(|| json!({}));
    match event_type {
        "turn_start" => {
            let input = payload.get("input").cloned().unwrap_or(Value::Null);
            let content = input.as_str().unwrap_or_default();
            if !content.is_empty() && content != "(resumed)" {
                let mut message = json!({
                    "role": "user",
                    "content": input,
                    "run_id": payload.get("run_id").cloned().unwrap_or(Value::Null),
                });
                if let Some(source) = payload.get("source").filter(|value| !value.is_null()) {
                    message["source"] = source.clone();
                }
                if let Some(attachments) = payload
                    .get("attachments")
                    .filter(|value| value.as_array().is_some_and(|items| !items.is_empty()))
                {
                    message["attachments"] = attachments.clone();
                }
                append_session_message(state_dir, session_id, &message)?;
            }
        }
        "assistant_message" => {
            let message = payload.get("message").cloned().unwrap_or_else(|| {
                json!({
                    "role": "assistant",
                    "content": payload.get("text").cloned().unwrap_or(Value::Null),
                    "reasoning": payload.get("reasoning").cloned().unwrap_or(Value::Null),
                    "tool_calls": payload.get("tool_calls").cloned().unwrap_or_else(|| json!([])),
                    "usage": payload.get("usage").cloned().unwrap_or(Value::Null),
                })
            });
            append_session_message(state_dir, session_id, &message)?;
        }
        "tool_finished" => {
            append_session_message(
                state_dir,
                session_id,
                &json!({
                    "role": "tool",
                    "tool_call_id": payload.get("tool_call_id").cloned().unwrap_or(Value::Null),
                    "name": payload.get("name").cloned().unwrap_or(Value::Null),
                    "content": payload.get("result").cloned().unwrap_or(Value::Null),
                    "error": payload.get("error").cloned().unwrap_or(Value::Null),
                }),
            )?;
        }
        "turn_end" | "interrupted" | "error" => {
            let conn = open_conn(&state_dir.join("core.db"))?;
            conn.execute(
                "UPDATE sessions SET recovery = ?1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?2",
                params![serde_json::to_string(&json!({"event": event_type, "payload": payload}))?, session_id],
            )?;
        }
        _ => {}
    }
    Ok(())
}

/// Consensus: list sessions, optionally filtered by workspace.
///
/// Returns `{"sessions": [...]}` in the SAME shape the FastAPI
/// `GET /v1/sessions` returned, so the frontend `getSessions` parses it
/// unchanged when switched to direct IPC.
pub fn list_sessions(state_dir: &Path, workspace: Option<&str>) -> Result<Value, ShadowReadError> {
    let db_path = state_dir.join("core.db");
    if !db_path.exists() {
        return Ok(json!({"sessions": []}));
    }
    let conn = open_conn(&db_path)?;
    let mut sessions = Vec::new();
    match workspace {
        Some(ws) => {
            let mut stmt = conn
                .prepare("SELECT * FROM sessions WHERE workspace = ?1 ORDER BY updated_at DESC")?;
            for r in stmt.query_map(params![ws], row_to_dto)? {
                sessions.push(r?);
            }
        }
        None => {
            let mut stmt = conn.prepare("SELECT * FROM sessions ORDER BY updated_at DESC")?;
            for r in stmt.query_map(params![], row_to_dto)? {
                sessions.push(r?);
            }
        }
    }
    Ok(json!({"sessions": sessions}))
}

/// Load a session's transcript: metadata + messages from its `.jsonl`.
pub fn get_session_messages(state_dir: &Path, session_id: &str) -> Result<Value, ShadowReadError> {
    let db_path = state_dir.join("core.db");
    let mut messages: Vec<Value> = load_messages(state_dir, session_id);
    if db_path.exists() {
        if let Ok(conn) = open_conn(&db_path) {
            // Backfill the inline `messages` column for legacy rows not yet
            // migrated to .jsonl (parsed as JSON each one).
            if let Ok(mut stmt) =
                conn.prepare("SELECT messages FROM sessions WHERE session_id = ?1")
            {
                if let Ok(mut rows) =
                    stmt.query_map(params![session_id], |r| r.get::<_, Option<String>>(0))
                {
                    if let Some(Ok(Some(inline))) = rows.next() {
                        if let Ok(parsed) = serde_json::from_str::<Vec<Value>>(&inline) {
                            if messages.is_empty() {
                                messages = parsed;
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(json!({"messages": messages}))
}

/// Rename a session's title.
pub fn rename_session(
    state_dir: &Path,
    session_id: &str,
    title: &str,
) -> Result<Value, ShadowReadError> {
    let db_path = state_dir.join("core.db");
    if !db_path.exists() {
        return Ok(json!({"ok": false, "error": "state db missing"}));
    }
    let conn = open_conn(&db_path)?;
    let n = conn.execute(
        "UPDATE sessions SET title = ?1, renamed = 1 WHERE session_id = ?2",
        params![title, session_id],
    )?;
    Ok(json!({"ok": n > 0}))
}

/// Toggle pinned/archived flags on a session.
pub fn set_session_flags(
    state_dir: &Path,
    session_id: &str,
    pinned: Option<bool>,
    archived: Option<bool>,
) -> Result<Value, ShadowReadError> {
    let db_path = state_dir.join("core.db");
    if !db_path.exists() {
        return Ok(json!({"ok": false, "error": "state db missing"}));
    }
    let conn = open_conn(&db_path)?;
    if let Some(p) = pinned {
        conn.execute(
            "UPDATE sessions SET pinned = ?1 WHERE session_id = ?2",
            params![if p { 1 } else { 0 }, session_id],
        )?;
    }
    if let Some(a) = archived {
        conn.execute(
            "UPDATE sessions SET archived = ?1 WHERE session_id = ?2",
            params![if a { 1 } else { 0 }, session_id],
        )?;
    }
    Ok(json!({"ok": true}))
}

/// Delete a session: remove its row and its transcript file.
pub fn delete_session(state_dir: &Path, session_id: &str) -> Result<Value, ShadowReadError> {
    let db_path = state_dir.join("core.db");
    let mut deleted_row = false;
    if db_path.exists() {
        let conn = open_conn(&db_path)?;
        let n = conn.execute(
            "DELETE FROM sessions WHERE session_id = ?1",
            params![session_id],
        )?;
        deleted_row = n > 0;
    }
    let jl = conversations_dir(state_dir).join(format!("{session_id}.jsonl"));
    let _ = fs::remove_file(&jl);
    Ok(json!({"ok": deleted_row, "session_id": session_id}))
}

/// List recently-used workspaces (distinct workspace column from sessions).
pub fn list_recent_workspaces(state_dir: &Path) -> Result<Value, ShadowReadError> {
    let db_path = state_dir.join("core.db");
    if !db_path.exists() {
        return Ok(json!({"workspaces": []}));
    }
    let conn = open_conn(&db_path)?;
    let mut stmt = conn.prepare(
        "SELECT workspace, MAX(updated_at) AS last FROM sessions \
         WHERE workspace != '' GROUP BY workspace ORDER BY last DESC",
    )?;
    let rows = stmt.query_map(params![], |r| {
        Ok(json!({
            "path": r.get::<_, String>("workspace")?,
            "name": Path::new(&r.get::<_, String>("workspace")?).file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
            "exists": Path::new(&r.get::<_, String>("workspace")?).is_dir(),
        }))
    })?;
    let mut workspaces = Vec::new();
    for r in rows {
        workspaces.push(r?);
    }
    Ok(json!({"workspaces": workspaces}))
}

fn session_workspace(state_dir: &Path, session_id: &str) -> Result<String, ShadowReadError> {
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.query_row(
        "SELECT workspace FROM sessions WHERE session_id = ?1",
        params![session_id],
        |row| row.get::<_, Option<String>>(0),
    )
    .map(|value| value.unwrap_or_default())
    .map_err(ShadowReadError::from)
}

pub fn get_session_workspace(
    state_dir: &Path,
    session_id: &str,
) -> Result<String, ShadowReadError> {
    session_workspace(state_dir, session_id)
}

fn rewrite_messages(
    state_dir: &Path,
    session_id: &str,
    messages: &[Value],
) -> Result<(), ShadowReadError> {
    if !safe_session_id(session_id) {
        return Err(ShadowReadError::Parse("unsafe session id".to_string()));
    }
    fs::create_dir_all(conversations_dir(state_dir))?;
    let path = conversations_dir(state_dir).join(format!("{session_id}.jsonl"));
    let mut file = fs::File::create(path)?;
    for message in messages {
        serde_json::to_writer(&mut file, message)?;
        file.write_all(b"\n")?;
    }
    file.flush()?;
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.execute(
        "UPDATE sessions SET n_msgs = ?1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?2",
        params![messages.len() as i64, session_id],
    )?;
    Ok(())
}

/// Truncate a transcript from `index` onward and return the original user text
/// for composer prefill.
pub fn revert_session(
    state_dir: &Path,
    session_id: &str,
    index: usize,
) -> Result<Value, ShadowReadError> {
    let messages = load_messages(state_dir, session_id);
    let Some(target) = messages.get(index) else {
        return Ok(json!({"ok": false, "error": "message index is out of range"}));
    };
    let text = target
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    rewrite_messages(state_dir, session_id, &messages[..index])?;
    Ok(json!({"ok": true, "text": text}))
}

pub fn set_reasoning_effort(
    state_dir: &Path,
    session_id: &str,
    effort: &str,
) -> Result<Value, ShadowReadError> {
    if !matches!(effort, "auto" | "low" | "medium" | "high") {
        return Ok(json!({"ok": false, "error": "unsupported reasoning effort"}));
    }
    let conn = open_conn(&state_dir.join("core.db"))?;
    let changed = conn.execute(
        "UPDATE sessions SET reasoning_effort = ?1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?2",
        params![effort, session_id],
    )?;
    Ok(json!({"ok": changed > 0, "reasoning_effort": effort}))
}

pub fn get_reasoning_effort(state_dir: &Path, session_id: &str) -> Result<String, ShadowReadError> {
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.query_row(
        "SELECT reasoning_effort FROM sessions WHERE session_id = ?1",
        params![session_id],
        |row| row.get::<_, Option<String>>(0),
    )
    .map(|value| value.unwrap_or_else(|| "auto".to_string()))
    .map_err(ShadowReadError::from)
}

pub fn set_session_mode(
    state_dir: &Path,
    session_id: &str,
    mode: &str,
) -> Result<Value, ShadowReadError> {
    if !matches!(mode, "interactive" | "plan" | "unattended") {
        return Ok(json!({"ok": false, "error": "unsupported session mode"}));
    }
    let conn = open_conn(&state_dir.join("core.db"))?;
    let changed = conn.execute(
        "UPDATE sessions SET mode = ?1, unattended = ?2, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?3",
        params![mode, (mode == "unattended") as i64, session_id],
    )?;
    Ok(json!({"ok": changed > 0, "mode": mode}))
}

pub fn get_unattended(state_dir: &Path, session_id: &str) -> Result<bool, ShadowReadError> {
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.query_row(
        "SELECT unattended FROM sessions WHERE session_id = ?1",
        params![session_id],
        |row| Ok(row.get::<_, i64>(0)? != 0),
    )
    .map_err(ShadowReadError::from)
}

pub fn get_session_recovery(
    state_dir: &Path,
    session_id: &str,
) -> Result<Option<Value>, ShadowReadError> {
    if !safe_session_id(session_id) {
        return Err(ShadowReadError::Parse("unsafe session id".to_string()));
    }
    let db_path = state_dir.join("core.db");
    if !db_path.exists() {
        return Ok(None);
    }
    let conn = open_conn(&db_path)?;
    let mut stmt = conn.prepare("SELECT recovery FROM sessions WHERE session_id = ?1")?;
    let mut rows = stmt.query(params![session_id])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };
    let raw: Option<String> = row.get(0)?;
    raw.filter(|value| !value.trim().is_empty())
        .map(|value| serde_json::from_str::<Value>(&value).map_err(ShadowReadError::from))
        .transpose()
}

pub fn set_unattended(
    state_dir: &Path,
    session_id: &str,
    unattended: bool,
) -> Result<Value, ShadowReadError> {
    let conn = open_conn(&state_dir.join("core.db"))?;
    let changed = conn.execute(
        "UPDATE sessions SET unattended = ?1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?2",
        params![unattended as i64, session_id],
    )?;
    Ok(json!({"ok": changed > 0, "unattended": unattended}))
}

fn parse_roots(raw: Option<String>) -> Vec<Value> {
    raw.and_then(|value| serde_json::from_str::<Vec<Value>>(&value).ok())
        .unwrap_or_default()
}

pub fn list_roots(state_dir: &Path, session_id: &str) -> Result<Value, ShadowReadError> {
    let conn = open_conn(&state_dir.join("core.db"))?;
    let (workspace, extra): (Option<String>, Option<String>) = conn.query_row(
        "SELECT workspace, extra_roots FROM sessions WHERE session_id = ?1",
        params![session_id],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let mut roots = Vec::new();
    if let Some(workspace) = workspace.filter(|value| !value.is_empty()) {
        let path = PathBuf::from(&workspace);
        roots.push(json!({
            "path": workspace,
            "writable": true,
            "label": path.file_name().map(|name| name.to_string_lossy().to_string()).unwrap_or_default(),
            "primary": true,
            "exists": path.is_dir(),
        }));
    }
    roots.extend(parse_roots(extra).into_iter().map(|mut root| {
        if let Some(object) = root.as_object_mut() {
            let path = object
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            object.insert("primary".to_string(), Value::Bool(false));
            object.insert("exists".to_string(), Value::Bool(Path::new(&path).is_dir()));
            object.entry("label".to_string()).or_insert_with(|| {
                Value::String(
                    Path::new(&path)
                        .file_name()
                        .map(|name| name.to_string_lossy().to_string())
                        .unwrap_or_default(),
                )
            });
        }
        root
    }));
    Ok(json!({"roots": roots}))
}

pub fn add_root(
    state_dir: &Path,
    session_id: &str,
    path: &str,
    writable: bool,
) -> Result<Value, ShadowReadError> {
    let canonical = PathBuf::from(path)
        .canonicalize()
        .map_err(|error| ShadowReadError::Parse(format!("root is unavailable: {error}")))?;
    if !canonical.is_dir() {
        return Ok(json!({"ok": false, "error": "root must be a directory"}));
    }
    let conn = open_conn(&state_dir.join("core.db"))?;
    let extra = conn.query_row(
        "SELECT extra_roots FROM sessions WHERE session_id = ?1",
        params![session_id],
        |row| row.get::<_, Option<String>>(0),
    )?;
    let canonical = canonical.to_string_lossy().to_string();
    let mut roots = parse_roots(extra);
    if let Some(root) = roots
        .iter_mut()
        .find(|root| root.get("path").and_then(Value::as_str) == Some(canonical.as_str()))
    {
        root["writable"] = Value::Bool(writable);
    } else {
        roots.push(json!({"path": canonical, "writable": writable}));
    }
    conn.execute(
        "UPDATE sessions SET extra_roots = ?1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?2",
        params![serde_json::to_string(&roots)?, session_id],
    )?;
    let mut response = list_roots(state_dir, session_id)?;
    response["ok"] = Value::Bool(true);
    Ok(response)
}

pub fn remove_root(
    state_dir: &Path,
    session_id: &str,
    path: &str,
) -> Result<Value, ShadowReadError> {
    let conn = open_conn(&state_dir.join("core.db"))?;
    let extra = conn.query_row(
        "SELECT extra_roots FROM sessions WHERE session_id = ?1",
        params![session_id],
        |row| row.get::<_, Option<String>>(0),
    )?;
    let mut roots = parse_roots(extra);
    roots.retain(|root| root.get("path").and_then(Value::as_str) != Some(path));
    conn.execute(
        "UPDATE sessions SET extra_roots = ?1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?2",
        params![serde_json::to_string(&roots)?, session_id],
    )?;
    let mut response = list_roots(state_dir, session_id)?;
    response["ok"] = Value::Bool(true);
    Ok(response)
}

fn trust_path(state_dir: &Path) -> PathBuf {
    state_dir.join("workspace_trust.json")
}

fn trusted_workspace_paths(state_dir: &Path) -> Result<Vec<String>, ShadowReadError> {
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

fn requested_commands(workspace: &Path) -> Vec<String> {
    let Ok(text) = fs::read_to_string(workspace.join(".delta").join("config.toml")) else {
        return Vec::new();
    };
    let Some(start) = text.find("allowed_commands") else {
        return Vec::new();
    };
    let tail = &text[start..];
    let Some(open) = tail.find('[') else {
        return Vec::new();
    };
    let Some(close) = tail[open + 1..].find(']') else {
        return Vec::new();
    };
    tail[open + 1..open + 1 + close]
        .split(',')
        .map(|item| item.trim().trim_matches(['\'', '"']).to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

fn workspace_trust_dto_from_paths(workspace: &Path, trusted_paths: &[String]) -> Value {
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

fn workspace_trust_dto(state_dir: &Path, workspace: &Path) -> Result<Value, ShadowReadError> {
    let trusted_paths = trusted_workspace_paths(state_dir)?;
    Ok(workspace_trust_dto_from_paths(workspace, &trusted_paths))
}

pub fn open_workspace(
    state_dir: &Path,
    path: &str,
    create: bool,
) -> Result<Value, ShadowReadError> {
    let requested = PathBuf::from(path);
    if create && !requested.exists() {
        fs::create_dir_all(&requested)?;
    }
    let canonical = requested
        .canonicalize()
        .map_err(|error| ShadowReadError::Parse(format!("workspace is unavailable: {error}")))?;
    if !canonical.is_dir() {
        return Ok(json!({"ok": false, "path": path, "error": "workspace must be a directory"}));
    }
    let canonical_text = canonical.to_string_lossy().to_string();
    let command_trust = workspace_trust_dto(state_dir, &canonical)?;
    let conn = open_conn(&state_dir.join("core.db"))?;
    conn.execute(
        "INSERT INTO workspaces (path, last_used) VALUES (?1, CURRENT_TIMESTAMP)
         ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
        params![canonical_text],
    )?;
    let git_branch = std::process::Command::new("git")
        .args(["-C", &canonical_text, "branch", "--show-current"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|branch| branch.trim().to_string())
        .filter(|branch| !branch.is_empty());
    Ok(json!({
        "ok": true,
        "path": canonical_text,
        "git_branch": git_branch,
        "command_trust": command_trust,
    }))
}

pub fn list_trusted_workspaces(state_dir: &Path) -> Result<Value, ShadowReadError> {
    let paths = trusted_workspace_paths(state_dir)?;
    let workspaces = paths
        .iter()
        .map(|path| workspace_trust_dto_from_paths(Path::new(path), &paths))
        .collect::<Vec<_>>();
    Ok(json!({"workspaces": workspaces}))
}

pub fn set_workspace_trusted(
    state_dir: &Path,
    path: &str,
    trusted: bool,
) -> Result<Value, ShadowReadError> {
    let canonical = PathBuf::from(path)
        .canonicalize()
        .map_err(|error| ShadowReadError::Parse(format!("workspace is unavailable: {error}")))?;
    let canonical_text = canonical.to_string_lossy().to_string();
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
}

fn session_run_ids(state_dir: &Path, session_id: &str) -> Vec<String> {
    let mut run_ids = Vec::new();
    for message in load_messages(state_dir, session_id) {
        if let Some(run_id) = message.get("run_id").and_then(Value::as_str) {
            if !run_ids.iter().any(|value| value == run_id) {
                run_ids.push(run_id.to_string());
            }
        }
    }
    run_ids
}

pub fn list_artifacts(state_dir: &Path, session_id: &str) -> Result<Value, ShadowReadError> {
    let ledger_path = state_dir.join("run_events.db");
    if !ledger_path.exists() {
        return Ok(json!({"artifacts": []}));
    }
    let workspace = session_workspace(state_dir, session_id)?;
    let reader = crate::artifact::ArtifactReader::open(ledger_path)?;
    let mut artifacts = Vec::new();
    for run_id in session_run_ids(state_dir, session_id) {
        for artifact in reader.list_for_run(&run_id)? {
            let absolute = PathBuf::from(&workspace).join(&artifact.path);
            let metadata = fs::metadata(&absolute).ok();
            let modified_at = metadata
                .as_ref()
                .and_then(|metadata| metadata.modified().ok())
                .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|duration| duration.as_secs_f64())
                .unwrap_or_default();
            artifacts.push(json!({
                "path": artifact.path,
                "abs_path": absolute,
                "name": absolute.file_name().map(|name| name.to_string_lossy().to_string()).unwrap_or_default(),
                "kind": artifact.kind,
                "size": metadata.map(|metadata| metadata.len() as i64).unwrap_or(artifact.size),
                "modified_at": modified_at,
                "sha256": artifact.sha256,
                "run_id": run_id,
                "incomplete": artifact.incomplete,
            }));
        }
    }
    Ok(json!({"artifacts": artifacts}))
}

pub fn resolve_artifact_path(
    state_dir: &Path,
    session_id: &str,
    path: &str,
) -> Result<PathBuf, ShadowReadError> {
    let artifacts = list_artifacts(state_dir, session_id)?;
    let registered = artifacts["artifacts"]
        .as_array()
        .is_some_and(|items| items.iter().any(|item| item["path"] == path));
    if !registered {
        return Err(ShadowReadError::Parse(
            "artifact is not registered for this session".to_string(),
        ));
    }
    let workspace = PathBuf::from(session_workspace(state_dir, session_id)?)
        .canonicalize()
        .map_err(|error| ShadowReadError::Parse(error.to_string()))?;
    let candidate = workspace.join(path);
    let canonical = candidate
        .canonicalize()
        .map_err(|error| ShadowReadError::Parse(error.to_string()))?;
    if !canonical.starts_with(&workspace) {
        return Err(ShadowReadError::Parse(
            "artifact escaped its workspace".to_string(),
        ));
    }
    Ok(canonical)
}

pub fn read_artifact(
    state_dir: &Path,
    session_id: &str,
    path: &str,
) -> Result<Value, ShadowReadError> {
    let absolute = resolve_artifact_path(state_dir, session_id, path)?;
    if absolute.is_dir() {
        let mut entries = Vec::new();
        for entry in fs::read_dir(&absolute)?.flatten().take(500) {
            let metadata = entry.metadata().ok();
            entries.push(json!({
                "name": entry.file_name().to_string_lossy(),
                "dir": metadata.as_ref().is_some_and(fs::Metadata::is_dir),
                "size": metadata.map(|value| value.len()).unwrap_or_default(),
            }));
        }
        return Ok(json!({"ok": true, "path": path, "kind": "folder", "entries": entries}));
    }
    let bytes = fs::read(&absolute)?;
    let extension = absolute
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let mime = match extension.as_str() {
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "gif" => Some("image/gif"),
        "webp" => Some("image/webp"),
        "svg" => Some("image/svg+xml"),
        "pdf" => Some("application/pdf"),
        _ => None,
    };
    if let Some(mime) = mime {
        let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
        return Ok(json!({
            "ok": true, "path": path, "kind": if extension == "pdf" { "pdf" } else { "image" },
            "data_url": format!("data:{mime};base64,{encoded}"), "truncated": false,
        }));
    }
    let max = 2 * 1024 * 1024;
    let truncated = bytes.len() > max;
    let content = String::from_utf8_lossy(&bytes[..bytes.len().min(max)]).to_string();
    Ok(json!({
        "ok": true, "path": path, "kind": "text", "content": content,
        "truncated": truncated,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list_sessions_empty_when_no_db() {
        let tmp = tempfile::tempdir().unwrap();
        let v = list_sessions(tmp.path(), None).unwrap();
        assert_eq!(v.get("sessions").unwrap().as_array().unwrap().len(), 0);
    }

    #[test]
    fn session_crud_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("core.db");
        let conn = open_conn(&db).unwrap();
        conn.execute(
            "INSERT INTO sessions (session_id, workspace, model, mode, title, agent, n_msgs) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params!["s1", "/ws", "gpt-5.5", "interactive", "Hello", "delta", 2],
        )
        .unwrap();

        let listed = list_sessions(tmp.path(), None).unwrap();
        let arr = listed.get("sessions").unwrap().as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["session_id"], "s1");
        assert_eq!(arr[0]["title"], "Hello");
        assert_eq!(arr[0]["messages"], 2);

        // rename
        let r = rename_session(tmp.path(), "s1", "Renamed").unwrap();
        assert_eq!(r["ok"], true);
        let listed2 = list_sessions(tmp.path(), None).unwrap();
        assert_eq!(listed2["sessions"][0]["title"], "Renamed");

        // flags
        set_session_flags(tmp.path(), "s1", Some(true), None).unwrap();
        let listed3 = list_sessions(tmp.path(), None).unwrap();
        assert_eq!(listed3["sessions"][0]["pinned"], true);

        // delete
        let d = delete_session(tmp.path(), "s1").unwrap();
        assert_eq!(d["ok"], true);
        let listed4 = list_sessions(tmp.path(), None).unwrap();
        assert_eq!(listed4["sessions"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn messages_from_jsonl() {
        let tmp = tempfile::tempdir().unwrap();
        let convo = conversations_dir(tmp.path());
        fs::create_dir_all(&convo).unwrap();
        fs::write(convo.join("s1.jsonl"), r#"{"role":"user","content":"hi"}"#).unwrap();
        let v = get_session_messages(tmp.path(), "s1").unwrap();
        let msgs = v.get("messages").unwrap().as_array().unwrap();
        assert_eq!(msgs.len(), 1);
        assert_eq!(msgs[0]["role"], "user");
    }

    #[test]
    fn legacy_agent_binding_migrates_once_without_touching_session_data() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("core.db");
        let conn = Connection::open(&db).unwrap();
        conn.execute_batch(SESSIONS_TABLE).unwrap();
        conn.execute(
            "INSERT INTO sessions
             (session_id, workspace, model, mode, title, agent, n_msgs, messages, pinned)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 1)",
            params![
                "legacy-1",
                "C:/work/保留",
                "legacy-model",
                "interactive",
                "Existing title",
                "code",
                1,
                r#"[{"role":"user","content":"keep me"}]"#,
            ],
        )
        .unwrap();
        drop(conn);

        let listed = list_sessions(tmp.path(), None).unwrap();
        assert_eq!(listed["sessions"][0]["agent"], "delta");
        assert_eq!(listed["sessions"][0]["workspace"], "C:/work/保留");

        let conn = open_conn(&db).unwrap();
        let row: (String, String, String, i64, String) = conn
            .query_row(
                "SELECT agent, workspace, messages, pinned, migration_marker
                 FROM sessions WHERE session_id = 'legacy-1'",
                params![],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(row.0, "delta");
        assert_eq!(row.1, "C:/work/保留");
        assert!(row.2.contains("keep me"));
        assert_eq!(row.3, 1);
        assert_eq!(row.4, "r6-agent-migration:code->delta");

        assert_eq!(migrate_legacy_session_rows(&conn).unwrap(), 0);
        ensure_session(tmp.path(), "legacy-1", None, "new-model").unwrap();
        let marker: String = conn
            .query_row(
                "SELECT migration_marker FROM sessions WHERE session_id = 'legacy-1'",
                params![],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(marker, "r6-agent-migration:code->delta");
    }

    #[test]
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
        assert!(open_workspace(state.path(), workspace.path().to_str().unwrap(), false).is_err());
        assert!(!state.path().join("core.db").exists());
        assert!(
            set_workspace_trusted(state.path(), workspace.path().to_str().unwrap(), true).is_err()
        );
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

    #[test]
    fn workspace_filter_and_recent() {
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join("core.db");
        let conn = open_conn(&db).unwrap();
        conn.execute(
            "INSERT INTO sessions (session_id, workspace, model, mode, title) VALUES (?1,?2,?3,?4,?5)",
            params!["a", "/wsA", "m", "interactive", "A"],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO sessions (session_id, workspace, model, mode, title) VALUES (?1,?2,?3,?4,?5)",
            params!["b", "/wsB", "m", "interactive", "B"],
        )
        .unwrap();
        let filtered = list_sessions(tmp.path(), Some("/wsA")).unwrap();
        let arr = filtered["sessions"].as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["session_id"], "a");

        let recent = list_recent_workspaces(tmp.path()).unwrap();
        let ws = recent["workspaces"].as_array().unwrap();
        assert_eq!(ws.len(), 2);
    }
}
