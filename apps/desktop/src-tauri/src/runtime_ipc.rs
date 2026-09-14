//! R6 Tauri Runtime IPC — in-process Runtime Host.
//!
//! Replaces the Python sidecar + localhost proxy with direct Tauri
//! commands. The React frontend calls `invoke("runtime_run", {...})`
//! instead of routing through a localhost service.
//!
//! Runtime events (turn_start, assistant_delta, tool_finished, etc.)
//! are emitted via `app.emit("delta-runtime-event", frame)` so the
//! frontend's existing event-parsing code (`parseRuntimeEvent`) works
//! unchanged — it just receives from Tauri events instead of WebSocket.

use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State};

use delta_runtime_native::{
    ApplicationStore, AutomationStore, CapabilityHost, EventSink, McpStore, MemoryStore,
    ModelAuthority, RuntimeAuthorities, RuntimeConfig, RuntimeHandle, RuntimeHost, SkillStore,
};

struct TauriEventSink {
    app: AppHandle,
    state_dir: std::path::PathBuf,
    automations: Arc<Mutex<AutomationStore>>,
}

impl EventSink for TauriEventSink {
    fn emit(&self, frame: Value) {
        let _ = delta_runtime_native::control_plane::record_runtime_event(&self.state_dir, &frame);
        if frame.get("type").and_then(Value::as_str) == Some("turn_end") {
            if let (Some(session_id), Some(status)) = (
                frame.get("sessionId").and_then(Value::as_str),
                frame
                    .get("payload")
                    .and_then(|payload| payload.get("status"))
                    .and_then(Value::as_str),
            ) {
                if session_id.starts_with("__run__") {
                    let _ = self
                        .automations
                        .lock()
                        .unwrap()
                        .finalize_session(session_id, status);
                }
            }
        }
        let _ = self.app.emit("delta-runtime-event", frame);
    }
}

pub struct RuntimeRegistry {
    hosts: Mutex<HashMap<String, Arc<RuntimeHandle>>>,
    models: Mutex<ModelAuthority>,
    authorities: RuntimeAuthorities,
    capabilities: Arc<CapabilityHost>,
    memory: Arc<MemoryStore>,
    automations: Arc<Mutex<AutomationStore>>,
    mcp: Arc<McpStore>,
    skills: Arc<SkillStore>,
    application: Arc<ApplicationStore>,
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
            memory: Arc::new(MemoryStore::open(state_dir()).expect("initialize Rust memory")),
            automations: Arc::new(Mutex::new(
                AutomationStore::open(state_dir()).expect("initialize Rust automations"),
            )),
            mcp: Arc::new(McpStore::open(state_dir()).expect("initialize Rust MCP authority")),
            skills: Arc::new(
                SkillStore::open(state_dir()).expect("initialize Rust skills authority"),
            ),
            application: Arc::new(
                ApplicationStore::open(state_dir())
                    .expect("initialize Rust application-state authority"),
            ),
        }
    }
}

pub fn init() -> RuntimeRegistry {
    RuntimeRegistry::new()
}

static APP_EVENT_SEQUENCE: AtomicU64 = AtomicU64::new(0);

pub fn start_scheduler(app: AppHandle) {
    std::thread::Builder::new()
        .name("delta-rust-scheduler".to_string())
        .spawn(move || loop {
            let state = app.state::<RuntimeRegistry>();
            let runs = state
                .automations
                .lock()
                .unwrap()
                .claim_due_runs()
                .unwrap_or_default();
            for run in runs {
                let session_id = run
                    .get("session_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                let model_id = state
                    .models
                    .lock()
                    .unwrap()
                    .settings()
                    .get("model")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                let accepted = start_runtime(
                    &app,
                    state.inner(),
                    session_id.clone(),
                    model_id,
                    run.get("prompt")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    run.get("workspace")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    None,
                    None,
                    Some("unattended".to_string()),
                    None,
                    None,
                    Some(json!({"automation_id": run.get("task_id"), "trigger": "scheduled"})),
                );
                if accepted.get("ok").and_then(Value::as_bool) == Some(true) {
                    let sequence = APP_EVENT_SEQUENCE.fetch_add(1, Ordering::SeqCst) + 1;
                    let _ = app.emit(
                        "delta-runtime-event",
                        json!({
                            "type": "automation_run_started", "version": 1,
                            "sessionId": Value::Null, "sequence": sequence,
                            "payload": {
                                "task_id": run.get("task_id"),
                                "task_title": run.get("task_title"),
                                "session_id": session_id,
                                "workspace": run.get("workspace"),
                            }
                        }),
                    );
                } else {
                    let _ = state
                        .automations
                        .lock()
                        .unwrap()
                        .finalize_session(&session_id, "failed");
                }
            }
            std::thread::sleep(Duration::from_secs(15));
        })
        .expect("start Rust automation scheduler");
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
    workspace: Option<String>,
    attachments: Option<Vec<Value>>,
    skill: Option<String>,
    mode: Option<String>,
    max_iterations: Option<usize>,
    max_retries: Option<u32>,
    source: Option<Value>,
) -> Value {
    start_runtime(
        &app,
        state.inner(),
        session_id,
        model_id,
        user_input,
        workspace,
        attachments,
        skill,
        mode,
        max_iterations,
        max_retries,
        source,
    )
}

#[allow(clippy::too_many_arguments)]
fn start_runtime(
    app: &AppHandle,
    state: &RuntimeRegistry,
    session_id: String,
    model_id: String,
    user_input: String,
    workspace: Option<String>,
    attachments: Option<Vec<Value>>,
    skill: Option<String>,
    mode: Option<String>,
    max_iterations: Option<usize>,
    max_retries: Option<u32>,
    source: Option<Value>,
) -> Value {
    let mut config = match state
        .models
        .lock()
        .unwrap()
        .resolve_runtime_config(&model_id)
    {
        Ok(config) => config,
        Err(error) => return json!({"ok": false, "error": error}),
    };
    let persisted_workspace =
        delta_runtime_native::control_plane::get_session_workspace(&state_dir(), &session_id)
            .ok()
            .filter(|value| !value.is_empty());
    let workspace = workspace
        .filter(|value| !value.is_empty())
        .or(persisted_workspace);
    if let Some(effort) =
        delta_runtime_native::control_plane::get_reasoning_effort(&state_dir(), &session_id)
            .ok()
            .filter(|value| value != "auto")
    {
        config.model_settings["reasoning_effort"] = Value::String(effort);
    }
    let mut prompt = delta_runtime_native::DELTA_AGENT.system_prompt.to_string();
    if mode.as_deref() == Some("plan") {
        prompt.push_str("\n\nPlan mode is active. Inspect and reason, but do not perform writes until the user explicitly approves the plan.");
    }
    if let Some(skill_name) = skill.as_deref() {
        let enabled = state
            .skills
            .session_rows(&session_id, workspace.as_deref())
            .ok()
            .and_then(|rows| {
                rows.into_iter().find(|row| {
                    row.get("name").and_then(Value::as_str) == Some(skill_name)
                        && row.get("enabled").and_then(Value::as_bool) == Some(true)
                })
            })
            .is_some();
        if !enabled {
            return json!({"ok": false, "error": format!("skill is unavailable for this session: {skill_name}")});
        }
        if let Ok(skills) = state.skills.list(workspace.as_deref()) {
            if let Some(selected) = skills
                .into_iter()
                .find(|row| row.get("name").and_then(Value::as_str) == Some(skill_name))
            {
                prompt.push_str("\n\nActive skill instructions:\n");
                prompt.push_str(
                    selected
                        .get("instructions")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                );
            }
        }
    }
    let config = RuntimeConfig {
        max_iterations: max_iterations.unwrap_or(12),
        max_retries: max_retries.unwrap_or(2),
        system_prompt: Some(prompt),
        workspace,
        unattended: delta_runtime_native::control_plane::get_unattended(&state_dir(), &session_id)
            .unwrap_or(false),
        ..config
    };
    if let Err(error) = delta_runtime_native::control_plane::ensure_session(
        &state_dir(),
        &session_id,
        config
            .workspace
            .as_deref()
            .filter(|value| !value.is_empty()),
        &model_id,
    ) {
        return json!({"ok": false, "error": error.to_string()});
    }
    if let Some(mode) = mode.as_deref() {
        match delta_runtime_native::control_plane::set_session_mode(&state_dir(), &session_id, mode)
        {
            Ok(value) if value.get("ok").and_then(Value::as_bool) == Some(false) => return value,
            Err(error) => return json!({"ok": false, "error": error.to_string()}),
            _ => {}
        }
    }
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
        let sink = Arc::new(TauriEventSink {
            app: app.clone(),
            state_dir: state_dir(),
            automations: state.automations.clone(),
        });
        let mut host = RuntimeHost::new(&session_id, config)
            .with_authorities(state.authorities.clone())
            .with_tools(state.capabilities.tool_schemas())
            .with_tool_executor(state.capabilities.clone())
            .with_event_sink(sink);
        if let Ok(messages) =
            delta_runtime_native::control_plane::get_session_messages(&state_dir(), &session_id)
        {
            if let Some(messages) = messages.get("messages").and_then(Value::as_array).cloned() {
                host = host.with_messages(messages);
            }
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
    let accepted = handle.run_with_attachments(user_input, attachments.unwrap_or_default(), source);
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
pub fn runtime_directory_response(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    granted: bool,
    path: Option<String>,
    writable: bool,
    tool_call_id: Option<String>,
) -> Value {
    let Some(handle) = state.hosts.lock().unwrap().get(&session_id).cloned() else {
        return json!({"ok": false, "error": format!("session not found: {session_id}")});
    };
    match handle.resolve_interaction(
        "directory",
        tool_call_id.as_deref(),
        json!({"granted": granted, "path": path, "writable": writable}),
    ) {
        Ok(id) => json!({"ok": true, "toolCallId": id}),
        Err(error) => json!({"ok": false, "error": error}),
    }
}

#[tauri::command]
pub fn runtime_plan_response(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    approved: bool,
    mode: Option<String>,
    feedback: Option<String>,
    tool_call_id: Option<String>,
) -> Value {
    let Some(handle) = state.hosts.lock().unwrap().get(&session_id).cloned() else {
        return json!({"ok": false, "error": format!("session not found: {session_id}")});
    };
    match handle.resolve_interaction(
        "plan",
        tool_call_id.as_deref(),
        json!({"approved": approved, "mode": mode, "feedback": feedback}),
    ) {
        Ok(id) => json!({"ok": true, "toolCallId": id}),
        Err(error) => json!({"ok": false, "error": error}),
    }
}

#[tauri::command]
pub fn runtime_question_response(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    answer: String,
    tool_call_id: Option<String>,
) -> Value {
    let Some(handle) = state.hosts.lock().unwrap().get(&session_id).cloned() else {
        return json!({"ok": false, "error": format!("session not found: {session_id}")});
    };
    match handle.resolve_interaction(
        "question",
        tool_call_id.as_deref(),
        json!({"answer": answer}),
    ) {
        Ok(id) => json!({"ok": true, "toolCallId": id}),
        Err(error) => json!({"ok": false, "error": error}),
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
pub fn runtime_set_mode(session_id: String, mode: String) -> Value {
    authority_result(delta_runtime_native::control_plane::set_session_mode(
        &state_dir(),
        &session_id,
        &mode,
    ))
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
pub fn settings_set_pdf(state: State<'_, RuntimeRegistry>, patch: Value) -> Value {
    authority_result(state.models.lock().unwrap().set_pdf_settings(&patch))
}

#[tauri::command]
pub fn settings_set_compaction(state: State<'_, RuntimeRegistry>, patch: Value) -> Value {
    authority_result(state.models.lock().unwrap().set_compaction_settings(&patch))
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

fn authority_result<E: std::fmt::Display>(result: Result<Value, E>) -> Value {
    result.unwrap_or_else(|error| json!({"ok": false, "error": error.to_string()}))
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

#[tauri::command]
pub fn workspace_open(path: String, create: bool) -> Value {
    match delta_runtime_native::control_plane::open_workspace(&state_dir(), &path, create) {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "path": path, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn workspaces_trusted() -> Value {
    match delta_runtime_native::control_plane::list_trusted_workspaces(&state_dir()) {
        Ok(value) => value,
        Err(error) => json!({"workspaces": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn workspace_set_trusted(path: String, trusted: bool) -> Value {
    match delta_runtime_native::control_plane::set_workspace_trusted(&state_dir(), &path, trusted) {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "path": path, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_revert(session_id: String, index: usize) -> Value {
    match delta_runtime_native::control_plane::revert_session(&state_dir(), &session_id, index) {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_set_reasoning(session_id: String, effort: String) -> Value {
    match delta_runtime_native::control_plane::set_reasoning_effort(
        &state_dir(),
        &session_id,
        &effort,
    ) {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_roots(session_id: String) -> Value {
    match delta_runtime_native::control_plane::list_roots(&state_dir(), &session_id) {
        Ok(value) => value,
        Err(error) => json!({"roots": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_add_root(session_id: String, path: String, writable: bool) -> Value {
    match delta_runtime_native::control_plane::add_root(&state_dir(), &session_id, &path, writable)
    {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_remove_root(session_id: String, path: String) -> Value {
    match delta_runtime_native::control_plane::remove_root(&state_dir(), &session_id, &path) {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_get_unattended(session_id: String) -> Value {
    match delta_runtime_native::control_plane::get_unattended(&state_dir(), &session_id) {
        Ok(unattended) => json!({"ok": true, "unattended": unattended}),
        Err(error) => json!({"ok": false, "unattended": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_set_unattended(session_id: String, unattended: bool) -> Value {
    match delta_runtime_native::control_plane::set_unattended(&state_dir(), &session_id, unattended)
    {
        Ok(value) => value,
        Err(error) => json!({"ok": false, "unattended": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn inbox_list(
    state: State<'_, RuntimeRegistry>,
    session_id: Option<String>,
    item_state: Option<String>,
) -> Value {
    json!({
        "items": state
            .authorities
            .inbox()
            .list(session_id.as_deref(), item_state.as_deref())
    })
}

#[tauri::command]
pub fn inbox_resolve(state: State<'_, RuntimeRegistry>, id: String, resolution: String) -> Value {
    let inbox = state.authorities.inbox();
    let Some(item) = inbox.get(&id) else {
        return json!({"ok": false, "error": "inbox item not found"});
    };
    if item.state != "pending" {
        return json!({"ok": true, "resolution": item.resolution});
    }
    let Some(handle) = state.hosts.lock().unwrap().get(&item.session_id).cloned() else {
        return json!({"ok": false, "error": "the owning runtime is not active; resume the session first"});
    };
    let resolved = if item.kind == "approval" {
        let decision = match resolution.as_str() {
            "allow" | "approve" | "approved" | "yes" => "once",
            "always_tool" => "always_tool",
            "always_command" => "always_command",
            "always_task" => "always_task",
            _ => "deny",
        };
        handle
            .resolve_approval(item.tool_call_id.as_deref(), decision)
            .map(|_| ())
    } else {
        let response = match item.kind.as_str() {
            "question" => json!({"answer": resolution}),
            "directory" => {
                let parsed = serde_json::from_str::<Value>(&resolution).unwrap_or(Value::Null);
                if parsed.is_object() {
                    parsed
                } else {
                    json!({
                        "granted": matches!(resolution.as_str(), "allow" | "approve" | "approved" | "yes"),
                        "path": item.data.get("path").cloned().unwrap_or(Value::Null),
                        "writable": item.data.get("writable").cloned().unwrap_or(Value::Bool(false)),
                    })
                }
            }
            "plan" => json!({
                "approved": matches!(resolution.as_str(), "allow" | "approve" | "approved" | "yes"),
                "feedback": if matches!(resolution.as_str(), "allow" | "approve" | "approved" | "yes") { Value::Null } else { Value::String(resolution.clone()) },
            }),
            _ => return json!({"ok": false, "error": "unsupported inbox item kind"}),
        };
        handle
            .resolve_interaction(&item.kind, item.tool_call_id.as_deref(), response)
            .map(|_| ())
    };
    if let Err(error) = resolved {
        return json!({"ok": false, "error": error});
    }
    if let Err(error) = inbox.resolve(&id, &resolution) {
        return json!({"ok": false, "error": error.to_string()});
    }
    json!({"ok": true})
}

#[tauri::command]
pub fn artifacts_list(session_id: String) -> Value {
    match delta_runtime_native::control_plane::list_artifacts(&state_dir(), &session_id) {
        Ok(value) => value,
        Err(error) => json!({"artifacts": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn artifact_read(session_id: String, path: String) -> Value {
    match delta_runtime_native::control_plane::read_artifact(&state_dir(), &session_id, &path) {
        Ok(value) => value,
        Err(error) => {
            json!({"ok": false, "path": path, "kind": "unknown", "error": error.to_string()})
        }
    }
}

#[tauri::command]
pub fn artifact_resolve_path(session_id: String, path: String) -> Value {
    match delta_runtime_native::control_plane::resolve_artifact_path(
        &state_dir(),
        &session_id,
        &path,
    ) {
        Ok(path) => json!({"ok": true, "path": path}),
        Err(error) => json!({"ok": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn memory_list(state: State<'_, RuntimeRegistry>) -> Value {
    match state.memory.list() {
        Ok(memory) => json!({"memory": memory}),
        Err(error) => json!({"memory": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn memory_update(state: State<'_, RuntimeRegistry>, id: i64, content: String) -> Value {
    authority_result(state.memory.update(id, &content))
}

#[tauri::command]
pub fn memory_delete(state: State<'_, RuntimeRegistry>, id: i64) -> Value {
    authority_result(state.memory.delete(id))
}

#[tauri::command]
pub fn memory_delete_all(state: State<'_, RuntimeRegistry>) -> Value {
    authority_result(state.memory.delete_all())
}

#[tauri::command]
pub fn memory_settings(state: State<'_, RuntimeRegistry>) -> Value {
    state.memory.settings()
}

#[tauri::command]
pub fn memory_set_settings(state: State<'_, RuntimeRegistry>, patch: Value) -> Value {
    authority_result(state.memory.set_settings(&patch))
}

#[tauri::command]
pub fn automations_list(state: State<'_, RuntimeRegistry>) -> Value {
    match state.automations.lock().unwrap().list() {
        Ok(tasks) => json!({"tasks": tasks}),
        Err(error) => json!({"tasks": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn automation_create(state: State<'_, RuntimeRegistry>, payload: Value) -> Value {
    authority_result(state.automations.lock().unwrap().create(&payload))
}

#[tauri::command]
pub fn automation_get(state: State<'_, RuntimeRegistry>, id: String) -> Value {
    authority_result(state.automations.lock().unwrap().get(&id))
}

#[tauri::command]
pub fn automation_update(state: State<'_, RuntimeRegistry>, id: String, changes: Value) -> Value {
    authority_result(state.automations.lock().unwrap().update(&id, &changes))
}

#[tauri::command]
pub fn automation_delete(state: State<'_, RuntimeRegistry>, id: String) -> Value {
    authority_result(state.automations.lock().unwrap().delete(&id))
}

#[tauri::command]
pub fn automation_mark_seen(state: State<'_, RuntimeRegistry>, id: String) -> Value {
    authority_result(state.automations.lock().unwrap().mark_seen(&id))
}

#[tauri::command]
pub fn automation_prepare_run(state: State<'_, RuntimeRegistry>, id: String) -> Value {
    authority_result(state.automations.lock().unwrap().prepare_run(&id))
}

#[tauri::command]
pub fn automation_finalize_run(
    state: State<'_, RuntimeRegistry>,
    id: String,
    run_id: String,
) -> Value {
    authority_result(state.automations.lock().unwrap().finalize_run(&id, &run_id))
}

#[tauri::command]
pub fn scheduler_due(state: State<'_, RuntimeRegistry>) -> Value {
    match state.automations.lock().unwrap().due() {
        Ok(tasks) => json!({"tasks": tasks}),
        Err(error) => json!({"tasks": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn mcp_list(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"servers": state.mcp.list()})
}

#[tauri::command]
pub fn mcp_put(state: State<'_, RuntimeRegistry>, name: String, config: Value) -> Value {
    authority_result(state.mcp.put(&name, config))
}

#[tauri::command]
pub fn mcp_patch(state: State<'_, RuntimeRegistry>, name: String, changes: Value) -> Value {
    authority_result(state.mcp.patch(&name, &changes))
}

#[tauri::command]
pub fn mcp_delete(state: State<'_, RuntimeRegistry>, name: String) -> Value {
    authority_result(state.mcp.delete(&name))
}

#[tauri::command]
pub fn mcp_tools(name: String) -> Value {
    let _ = name;
    json!({"ok": true, "tools": []})
}

#[tauri::command]
pub fn mcp_reload() -> Value {
    json!({"ok": true})
}

#[tauri::command]
pub fn mcp_connect(name: String) -> Value {
    let _ = name;
    json!({"ok": true, "started": false})
}

#[tauri::command]
pub fn mcp_signout(name: String) -> Value {
    let _ = name;
    json!({"ok": true})
}

#[tauri::command]
pub fn audit_list(
    limit: Option<usize>,
    session_id: Option<String>,
    connector: Option<String>,
    tool: Option<String>,
) -> Value {
    let writer = match delta_runtime_native::ApprovalWriter::open(
        state_dir()
            .join("audit_events.db")
            .to_string_lossy()
            .as_ref(),
    ) {
        Ok(writer) => writer,
        Err(error) => return json!({"events": [], "error": error.to_string()}),
    };
    match writer.list(
        limit.unwrap_or(100).clamp(1, 500),
        session_id.as_deref(),
        connector.as_deref(),
        tool.as_deref(),
    ) {
        Ok(events) => json!({"events": events}),
        Err(error) => json!({"events": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn sources_list() -> Value {
    let path = state_dir().join("run_events.db");
    if !path.exists() {
        return json!({"sources": []});
    }
    match delta_runtime_native::SourceCitationReader::open(path)
        .and_then(|reader| reader.list_sources())
    {
        Ok(sources) => json!({"sources": sources}),
        Err(error) => json!({"sources": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn validations_list() -> Value {
    let path = state_dir().join("run_events.db");
    if !path.exists() {
        return json!({"validations": []});
    }
    match delta_runtime_native::ValidationReader::open(path)
        .and_then(|reader| reader.list_validations())
    {
        Ok(validations) => json!({"validations": validations}),
        Err(error) => json!({"validations": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn skills_list(state: State<'_, RuntimeRegistry>, workspace: Option<String>) -> Value {
    match state.skills.list(workspace.as_deref()) {
        Ok(skills) => json!({"skills": skills}),
        Err(error) => json!({"skills": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn skill_create(state: State<'_, RuntimeRegistry>, body: Value) -> Value {
    authority_result(state.skills.create(&body))
}

#[tauri::command]
pub fn skill_update(state: State<'_, RuntimeRegistry>, name: String, patch: Value) -> Value {
    authority_result(state.skills.update(
        &name,
        &patch,
        patch.get("workspace").and_then(Value::as_str),
    ))
}

#[tauri::command]
pub fn skill_delete(
    state: State<'_, RuntimeRegistry>,
    name: String,
    workspace: Option<String>,
) -> Value {
    authority_result(state.skills.delete(&name, workspace.as_deref()))
}

#[tauri::command]
pub fn skill_move(
    state: State<'_, RuntimeRegistry>,
    name: String,
    scope: String,
    workspace: Option<String>,
) -> Value {
    authority_result(state.skills.move_skill(&name, &scope, workspace.as_deref()))
}

#[tauri::command]
pub fn skill_resolve_folder(
    state: State<'_, RuntimeRegistry>,
    name: String,
    workspace: Option<String>,
) -> Value {
    match state.skills.resolve_folder(&name, workspace.as_deref()) {
        Ok(path) => json!({"ok": true, "path": path}),
        Err(error) => json!({"ok": false, "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn skill_stage_upload(
    state: State<'_, RuntimeRegistry>,
    data_b64: String,
    filename: String,
) -> Value {
    authority_result(state.skills.stage_upload(&data_b64, &filename))
}

#[tauri::command]
pub fn skill_confirm_upload(
    state: State<'_, RuntimeRegistry>,
    token: String,
    scope: String,
    workspace: Option<String>,
) -> Value {
    authority_result(
        state
            .skills
            .confirm_upload(&token, &scope, workspace.as_deref()),
    )
}

#[tauri::command]
pub fn session_skills(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    workspace: Option<String>,
) -> Value {
    match state.skills.session_rows(&session_id, workspace.as_deref()) {
        Ok(skills) => json!({"skills": skills}),
        Err(error) => json!({"skills": [], "error": error.to_string()}),
    }
}

#[tauri::command]
pub fn session_set_skill(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    skill: String,
    enabled: bool,
    clear: bool,
    workspace: Option<String>,
) -> Value {
    authority_result(state.skills.set_session(
        &session_id,
        &skill,
        enabled,
        clear,
        workspace.as_deref(),
    ))
}

#[tauri::command]
pub fn connectors_list(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"connectors": state.application.connectors()})
}

#[tauri::command]
pub fn connector_connect(
    state: State<'_, RuntimeRegistry>,
    name: String,
    fields: BTreeMap<String, String>,
) -> Value {
    authority_result(state.application.connect(&name, &fields))
}

#[tauri::command]
pub fn connector_disconnect(state: State<'_, RuntimeRegistry>, name: String) -> Value {
    authority_result(state.application.disconnect(&name))
}

#[tauri::command]
pub fn connector_update_tools(
    state: State<'_, RuntimeRegistry>,
    name: String,
    enabled: BTreeMap<String, bool>,
) -> Value {
    authority_result(state.application.update_tools(&name, &enabled))
}

#[tauri::command]
pub fn connector_action(
    state: State<'_, RuntimeRegistry>,
    name: String,
    action: String,
    payload: Value,
) -> Value {
    authority_result(state.application.action(&name, &action, &payload))
}

#[tauri::command]
pub fn session_connections(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {
    state.application.session_connections(&session_id)
}

#[tauri::command]
pub fn session_set_connection(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    connector: String,
    enabled: bool,
    clear: bool,
) -> Value {
    authority_result(state.application.set_session_connection(
        &session_id,
        &connector,
        enabled,
        clear,
    ))
}

#[tauri::command]
pub fn subscriptions_list(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"subscriptions": state.application.subscriptions()})
}

#[tauri::command]
pub fn subscription_add(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    channel: String,
) -> Value {
    authority_result(state.application.subscribe(&session_id, &channel))
}

#[tauri::command]
pub fn subscription_remove(
    state: State<'_, RuntimeRegistry>,
    session_id: String,
    channel: String,
) -> Value {
    authority_result(state.application.unsubscribe(&session_id, &channel))
}

#[tauri::command]
pub fn inbox_routing_list(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"bindings": state.application.inbox_bindings()})
}

#[tauri::command]
pub fn inbox_routing_set(
    state: State<'_, RuntimeRegistry>,
    name: String,
    channel: Option<String>,
    target: String,
) -> Value {
    authority_result(
        state
            .application
            .set_inbox_binding(&name, channel.as_deref(), &target),
    )
}

#[tauri::command]
pub fn unrouted_list(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"items": state.application.unrouted()})
}

#[tauri::command]
pub fn recent_channels(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"channels": state.application.recent_channels()})
}

#[tauri::command]
pub fn dm_route_get(state: State<'_, RuntimeRegistry>) -> Value {
    json!({"dm_session": state.application.dm_route()})
}

#[tauri::command]
pub fn dm_route_set(state: State<'_, RuntimeRegistry>, session_id: String) -> Value {
    authority_result(state.application.set_dm_route(&session_id))
}

fn closed_browser_state() -> Value {
    json!({
        "open": false, "url": "", "title": "", "status": "closed",
        "last_action": "", "last_result": "", "last_error": "",
        "screenshot_data_url": "", "updated_at": Value::Null, "controls": [],
    })
}

#[tauri::command]
pub fn browser_state() -> Value {
    closed_browser_state()
}

#[tauri::command]
pub fn browser_screenshot() -> Value {
    let mut state = closed_browser_state();
    state["ok"] = Value::Bool(false);
    state["error"] = Value::String("no controlled browser worker is active".to_string());
    state
}

#[tauri::command]
pub fn browser_close() -> Value {
    json!({"ok": true})
}
