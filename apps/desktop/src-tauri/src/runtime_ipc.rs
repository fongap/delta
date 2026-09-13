//! R6 Tauri Runtime IPC — in-process Runtime Host.
//!
//! Replaces the Python sidecar + localhost proxy with direct Tauri
//! commands. The React frontend calls `invoke("runtime_run", {...})`
//! instead of `fetch("http://127.0.0.1:PORT/v1/sessions/.../ws")`.
//!
//! Runtime events (turn_start, assistant_delta, tool_finished, etc.)
//! are emitted via `app.emit("delta-runtime-event", frame)` so the
//! frontend's existing event-parsing code (`parseRuntimeEvent`) works
//! unchanged — it just receives from Tauri events instead of WebSocket.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, State};

use delta_runtime_native::{EventSink, RuntimeConfig, RuntimeHandle, RuntimeHost};

struct TauriEventSink {
    app: AppHandle,
}

impl EventSink for TauriEventSink {
    fn emit(&self, frame: Value) {
        let _ = self.app.emit("delta-runtime-event", frame);
    }
}

pub struct RuntimeRegistry {
    hosts: Mutex<HashMap<String, Arc<RuntimeHandle>>>,
}

impl RuntimeRegistry {
    fn new() -> Self {
        Self {
            hosts: Mutex::new(HashMap::new()),
        }
    }
}

pub fn init() -> RuntimeRegistry {
    RuntimeRegistry::new()
}

fn resolve_config(
    model: String,
    protocol: String,
    api_key: String,
    base_url: String,
    settings: Option<Value>,
    system_prompt: Option<String>,
    workspace: Option<String>,
) -> RuntimeConfig {
    RuntimeConfig {
        model,
        protocol,
        api_key,
        base_url,
        max_iterations: 12,
        max_retries: 2,
        ttft_timeout: None,
        tool_timeout: None,
        model_settings: settings.unwrap_or(json!({})),
        system_prompt,
        workspace,
    }
}

#[tauri::command]
pub fn health() -> Value {
    json!({
        "status": "ok",
        "default_workspace": null,
        "model": "",
        "protocolVersion": 1,
        "capabilities": ["events.app-wide", "provider.custom", "session.message-revert", "session.reasoning-effort"],
    })
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub fn runtime_run(
    app: AppHandle,
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    model: String,
    protocol: String,
    api_key: String,
    base_url: String,
    user_input: String,
    tools: Option<Value>,
    settings: Option<Value>,
    system_prompt: Option<String>,
    workspace: Option<String>,
    messages: Option<Value>,
    max_iterations: Option<usize>,
    max_retries: Option<u32>,
    source: Option<Value>,
) -> Value {
    let config = resolve_config(
        model,
        protocol,
        api_key,
        base_url,
        settings.clone(),
        system_prompt.clone(),
        workspace.clone(),
    );
    let config = RuntimeConfig {
        max_iterations: max_iterations.unwrap_or(12),
        max_retries: max_retries.unwrap_or(2),
        ..config
    };
    let sink = Arc::new(TauriEventSink { app: app.clone() });
    let mut host = RuntimeHost::new(&session_id, config).with_event_sink(sink);
    if let Some(t) = tools {
        host = host.with_tools(t);
    }
    if let Some(msgs) = messages.and_then(|v| v.as_array().cloned()) {
        host = host.with_messages(msgs);
    }
    let handle = match RuntimeHandle::spawn(host) {
        Ok(handle) => Arc::new(handle),
        Err(error) => return json!({"ok": false, "error": error}),
    };

    // Registration is authoritative and happens before the worker can begin a
    // provider request. The registry lock protects only this short map update.
    {
        let mut hosts = state.hosts.lock().unwrap();
        if let Some(existing) = hosts.get(&session_id) {
            if existing.state().is_active() {
                return json!({
                    "ok": false,
                    "error": format!("session {session_id} already has an active run")
                });
            }
        }
        hosts.insert(session_id.clone(), handle.clone());
    }

    match handle.run(user_input, source) {
        Ok(run_id) => json!({
            "ok": true,
            "accepted": true,
            "runId": run_id,
            "state": handle.state(),
        }),
        Err(error) => json!({"ok": false, "error": error}),
    }
}

#[tauri::command]
pub fn runtime_resume(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.resume() {
            Ok(run_id) => json!({"ok": true, "accepted": true, "runId": run_id}),
            Err(error) => json!({"ok": false, "error": error}),
        },
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_retry(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.retry() {
            Ok(run_id) => json!({"ok": true, "accepted": true, "runId": run_id}),
            Err(error) => json!({"ok": false, "error": error}),
        },
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_steer(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    text: String,
    source: Option<Value>,
) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.steer(&text, source) {
            Ok(()) => json!({"ok": true, "accepted": true}),
            Err(error) => json!({"ok": false, "error": error}),
        },
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_follow_up(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    text: String,
    source: Option<Value>,
) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.follow_up(&text, source) {
            Ok(run_id) => json!({"ok": true, "accepted": true, "runId": run_id}),
            Err(error) => json!({"ok": false, "error": error}),
        },
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_cancel(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => json!({"ok": true, "cancelled": handle.cancel()}),
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_messages(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => json!({"messages": handle.messages(), "state": handle.state()}),
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_switch_model(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    model: String,
) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.switch_model(&model) {
            Ok(Some(notice)) => json!({"ok": true, "notice": notice}),
            Ok(None) => json!({"ok": true, "notice": Value::Null}),
            Err(error) => json!({"ok": false, "error": error}),
        },
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

#[tauri::command]
pub fn runtime_truncate(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    index: usize,
) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.truncate_messages(index) {
            Ok(len) => json!({"ok": true, "len": len}),
            Err(error) => json!({"ok": false, "error": error}),
        },
        None => json!({"ok": false, "error": format!("session not found: {session_id}")}),
    }
}

// ---------------------------------------------------------------------------
// R6 Application Control Plane — Session/Workspace commands.
// Read/write the persisted Delta state (core.db + conversations/*.jsonl) via
// delta_runtime_native::control_plane. Replaces the removed Python server.
// ---------------------------------------------------------------------------

/// Resolve the application state dir (mirrors the Tauri shell's `state_dir`).
fn state_dir() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("DELTA_STATE_DIR") {
        return std::path::PathBuf::from(d);
    }
    #[cfg(windows)]
    {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return std::path::PathBuf::from(appdata).join("delta");
        }
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    std::path::PathBuf::from(home).join(".config").join("delta")
}

#[tauri::command]
pub fn sessions_list(workspace: Option<String>) -> Value {
    match delta_runtime_native::control_plane::list_sessions(&state_dir(), workspace.as_deref()) {
        Ok(v) => v,
        Err(e) => json!({"sessions": [], "error": e.to_string()}),
    }
}

#[tauri::command]
pub fn session_messages(session_id: String) -> Value {
    match delta_runtime_native::control_plane::get_session_messages(&state_dir(), &session_id) {
        Ok(v) => v,
        Err(e) => json!({"messages": [], "error": e.to_string()}),
    }
}

#[tauri::command]
pub fn session_rename(session_id: String, title: String) -> Value {
    match delta_runtime_native::control_plane::rename_session(&state_dir(), &session_id, &title) {
        Ok(v) => v,
        Err(e) => json!({"ok": false, "error": e.to_string()}),
    }
}

#[tauri::command]
pub fn session_set_flags(
    session_id: String,
    pinned: Option<bool>,
    archived: Option<bool>,
) -> Value {
    match delta_runtime_native::control_plane::set_session_flags(
        &state_dir(),
        &session_id,
        pinned,
        archived,
    ) {
        Ok(v) => v,
        Err(e) => json!({"ok": false, "error": e.to_string()}),
    }
}

#[tauri::command]
pub fn session_delete(session_id: String) -> Value {
    match delta_runtime_native::control_plane::delete_session(&state_dir(), &session_id) {
        Ok(v) => v,
        Err(e) => json!({"ok": false, "error": e.to_string()}),
    }
}

#[tauri::command]
pub fn workspaces_recent() -> Value {
    match delta_runtime_native::control_plane::list_recent_workspaces(&state_dir()) {
        Ok(v) => v,
        Err(e) => json!({"workspaces": [], "error": e.to_string()}),
    }
}
