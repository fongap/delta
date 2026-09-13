//! R6 Application Control Plane — Rust authority for Session/Workspace state.
//!
//! Replaces the Python `SessionManager` / `ConversationStore` control plane. Reads
//! the persisted Delta state directly (SQLite `core.db` sessions index +
//! `conversations/<id>.jsonl` message logs), so existing user data keeps working.
//!
//! Ownership: the Rust core is the sole authority for session metadata and
//! workspace trust state. Writes go through this module (no Python facade).

use std::fs;
use std::io::BufRead;
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::{json, Value};

use crate::ShadowReadError;

const SESSIONS_TABLE: &str = "CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, mode TEXT,
    title TEXT, agent TEXT DEFAULT 'delta', n_msgs INTEGER DEFAULT 0, messages TEXT,
    extra_roots TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
    origin TEXT, origin_label TEXT,
    auto_title TEXT, renamed INTEGER DEFAULT 0,
    recovery TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);";

/// Start a read-write SQLite connection to `core.db`, ensuring the schema.
fn open_conn(db_path: &Path) -> Result<Connection, ShadowReadError> {
    let conn = Connection::open(db_path)?;
    conn.prepare(SESSIONS_TABLE)?.execute(params![])?;
    Ok(conn)
}

/// The conversations dir where `<session_id>.jsonl` lives.
fn conversations_dir(state_dir: &Path) -> PathBuf {
    state_dir.join("conversations")
}

/// Load messages from `<state_dir>/conversations/<id>.jsonl` (append-only log).
fn load_messages(state_dir: &Path, session_id: &str) -> Vec<Value> {
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
        "workspace": row.get::<_, String>("workspace")?,
        "agent": row.get::<_, String>("agent")?,
        "model": row.get::<_, String>("model")?,
        "mode": row.get::<_, String>("mode")?,
        "updated_at": updated_at,
        "messages": row.get::<_, i64>("n_msgs")?,
        "pinned": row.get::<_, i64>("pinned")? != 0,
        "archived": row.get::<_, i64>("archived")? != 0,
        "origin": origin,
    }))
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
