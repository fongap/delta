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

use delta_runtime_native::{
    CapabilityHost, EventSink, ModelAuthority, RuntimeAuthorities, RuntimeConfig, RuntimeHandle,
    RuntimeHost,
};

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
    models: Mutex<ModelAuthority>,
    authorities: RuntimeAuthorities,
    capabilities: Arc<CapabilityHost>,
}

impl RuntimeRegistry {
    fn new() -> Self {
        Self {
            hosts: Mutex::new(HashMap::new()),
            models: Mutex::new(ModelAuthority::new(state_dir())),
            authorities: RuntimeAuthorities::open(state_dir())
                .expect("initialize Rust runtime authorities"),
            capabilities: Arc::new(
                CapabilityHost::product_defaults().expect("initialize Rust capability host"),
            ),
        }
    }
}

pub fn init() -> RuntimeRegistry {
    RuntimeRegistry::new()
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
    model_id: String,
    user_input: String,
    system_prompt: Option<String>,
    workspace: Option<String>,
    messages: Option<Value>,
    max_iterations: Option<usize>,
    max_retries: Option<u32>,
    source: Option<Value>,
) -> Value {
    let config = match state
        .models
        .lock()
        .unwrap()
        .resolve_runtime_config(&model_id)
    {
        Ok(config) => config,
        Err(error) => return json!({"ok": false, "error": error}),
    };
    let config = RuntimeConfig {
        max_iterations: max_iterations.unwrap_or(12),
        max_retries: max_retries.unwrap_or(2),
        system_prompt,
        workspace,
        ..config
    };
    let mut hosts = state.hosts.lock().unwrap();
    let handle = if let Some(existing) = hosts.get(&session_id).cloned() {
        if existing.state().is_active() {
            return json!({
                "ok": false,
                "error": format!("session {session_id} already has an active run")
            });
        }
        if let Err(error) = existing.switch_runtime_config(config) {
            return json!({"ok": false, "error": error});
        }
        existing
    } else {
        let sink = Arc::new(TauriEventSink { app: app.clone() });
        let mut host = RuntimeHost::new(&session_id, config)
            .with_authorities(state.authorities.clone())
            .with_tools(state.capabilities.tool_schemas())
            .with_tool_executor(state.capabilities.clone())
            .with_event_sink(sink);
        if let Some(messages) = messages.and_then(|value| value.as_array().cloned()) {
            host = host.with_messages(messages);
        }
        let handle = match RuntimeHandle::spawn(host) {
            Ok(handle) => Arc::new(handle),
            Err(error) => return json!({"ok": false, "error": error}),
        };
        // Registration precedes `run()`, and the map lock stays held through
        // acceptance so two simultaneous starts cannot create detached loops.
        hosts.insert(session_id.clone(), handle.clone());
        handle
    };
    let accepted = handle.run(user_input, source);
    drop(hosts);

    match accepted {
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
pub fn runtime_approval(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    decision: String,
    tool_call_id: Option<String>,
) -> Value {
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.resolve_approval(tool_call_id.as_deref(), &decision) {
            Ok(resolved) => json!({"ok": true, "toolCallId": resolved, "decision": decision}),
            Err(error) => json!({"ok": false, "error": error}),
        },
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
    model_id: String,
) -> Value {
    let config = match state
        .models
        .lock()
        .unwrap()
        .resolve_runtime_config(&model_id)
    {
        Ok(config) => config,
        Err(error) => return json!({"ok": false, "error": error}),
    };
    let handle = state.hosts.lock().unwrap().get(&session_id).cloned();
    match handle {
        Some(handle) => match handle.switch_runtime_config(config) {
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
// R6 Provider / Model / Settings / Secrets authority.
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn settings_get(state: State<'_, RuntimeRegistry>) -> Value {
    state.models.lock().unwrap().settings()
}

#[tauri::command]
pub fn settings_set_model_key(state: State<'_, RuntimeRegistry>, api_key: String) -> Value {
    authority_result(state.models.lock().unwrap().set_provider(
        "openai",
        None,
        &json!({"api_key": api_key}),
    ))
}

#[tauri::command]
pub fn settings_set_default_model(state: State<'_, RuntimeRegistry>, model_id: String) -> Value {
    authority_result(state.models.lock().unwrap().set_default_model(&model_id))
}

#[tauri::command]
pub fn settings_add_model(state: State<'_, RuntimeRegistry>, model_id: String) -> Value {
    authority_result(state.models.lock().unwrap().add_model(&model_id))
}

#[tauri::command]
pub fn settings_remove_model(state: State<'_, RuntimeRegistry>, model_id: String) -> Value {
    authority_result(state.models.lock().unwrap().remove_model(&model_id))
}

#[tauri::command]
pub fn settings_set_onboarded(state: State<'_, RuntimeRegistry>, value: bool) -> Value {
    authority_result(state.models.lock().unwrap().set_onboarded(value))
}

#[tauri::command]
pub fn settings_set_language(state: State<'_, RuntimeRegistry>, language: String) -> Value {
    authority_result(state.models.lock().unwrap().set_language(&language))
}

#[tauri::command]
pub fn settings_set_context_bar(state: State<'_, RuntimeRegistry>, shown: bool) -> Value {
    authority_result(state.models.lock().unwrap().set_context_bar(shown))
}

#[tauri::command]
pub fn settings_set_sessions_peek(state: State<'_, RuntimeRegistry>, count: i64) -> Value {
    authority_result(state.models.lock().unwrap().set_sessions_peek(count))
}

#[tauri::command]
pub fn settings_set_scratch_base(state: State<'_, RuntimeRegistry>, path: String) -> Value {
    authority_result(state.models.lock().unwrap().set_scratch_base(&path))
}

#[tauri::command]
pub fn settings_set_nav_layout(state: State<'_, RuntimeRegistry>, layout: String) -> Value {
    authority_result(state.models.lock().unwrap().set_nav_layout(&layout))
}

#[tauri::command]
pub fn settings_set_pdf(state: State<'_, RuntimeRegistry>, patch: Value) -> Value {
    authority_result(state.models.lock().unwrap().set_pdf_settings(&patch))
}

#[tauri::command]
pub fn settings_set_compaction(state: State<'_, RuntimeRegistry>, patch: Value) -> Value {
    authority_result(state.models.lock().unwrap().set_compaction_settings(&patch))
}

#[tauri::command]
pub fn settings_set_surfaces() -> Value {
    json!({"ok": true, "surfaces": {"delta": true}})
}

#[tauri::command]
pub fn providers_list(state: State<'_, RuntimeRegistry>) -> Value {
    state.models.lock().unwrap().providers()
}

#[tauri::command]
pub fn provider_protocols(state: State<'_, RuntimeRegistry>) -> Value {
    state.models.lock().unwrap().protocols()
}

#[tauri::command]
pub fn provider_set(
    state: State<'_, RuntimeRegistry>,
    name: String,
    protocol: Option<String>,
    fields: Value,
) -> Value {
    authority_result(
        state
            .models
            .lock()
            .unwrap()
            .set_provider(&name, protocol.as_deref(), &fields),
    )
}

#[tauri::command]
pub fn provider_remove(state: State<'_, RuntimeRegistry>, name: String) -> Value {
    authority_result(state.models.lock().unwrap().remove_provider(&name))
}

#[tauri::command]
pub fn provider_verify(state: State<'_, RuntimeRegistry>, name: String, fields: Value) -> Value {
    state.models.lock().unwrap().verify_provider(&name, &fields)
}

#[tauri::command]
pub fn provider_fetch_models(
    state: State<'_, RuntimeRegistry>,
    name: String,
    fields: Value,
) -> Value {
    state.models.lock().unwrap().fetch_models(&name, &fields)
}

fn authority_result(result: Result<Value, String>) -> Value {
    result.unwrap_or_else(|error| json!({"ok": false, "error": error}))
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
