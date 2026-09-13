//! R6 Rust Runtime Host — the owned agent loop.
//!
//! Combines R1-R5.1 Rust authorities (provider, ledger, policy, tool_lifecycle,
//! idemlog, retry) into a complete runtime that drives a full turn:
//!
//! User Input -> Context -> Model -> Tool -> Tool Result -> Model Continue -> Artifact -> Complete
//!
//! Replaces the Python `TurnEngine` (core/engine.py). No Python dependency.
//!
//! Lifecycle:
//! - `run()` — fresh user input, event stream
//! - `resume()` — continue a suspended turn (pending tool calls)
//! - `retry()` — re-run after a provider error
//! - `steer()` — inject mid-turn steering text
//! - `follow_up()` — queue a follow-up turn for after completion
//! - `cancel()` — interrupt from any state

use std::collections::VecDeque;
use std::fs;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex, RwLock};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::approval::{ApprovalController, ApprovalDecision, ApprovalRecordInput, ApprovalWriter};
use crate::artifact::{ArtifactInput, ArtifactRegistryWriter};
use crate::checkpoint::{CheckpointRegisterInput, CheckpointWriter};
use crate::idemlog::IdempotencyWriter;
use crate::policy::{self, Decision, PolicyEvaluateInput, RiskLevel, RootEntry, ToolMetadata};
use crate::provider::{self, ProviderRequest};
use crate::tool_lifecycle::{self, PlanAction, ToolLifecyclePlanInput};
use crate::validation::ValidationWriter;
use crate::LedgerWriter;

const DEFAULT_MAX_ITERATIONS: usize = 12;

/// Sink for runtime events. The delta_core binary installs a stdout sink;
/// the in-process Tauri shell installs a Tauri-event sink.
pub trait EventSink: Send + Sync {
    fn emit(&self, frame: Value);
}

/// Default sink: write the frame as one JSON line to stdout.
pub struct StdoutSink;
impl EventSink for StdoutSink {
    fn emit(&self, frame: Value) {
        let mut stdout = std::io::stdout();
        let _ = writeln!(stdout, "{frame}");
        let _ = stdout.flush();
    }
}

/// Silent sink: drop every event (tests, headless runners).
pub struct NullSink;
impl EventSink for NullSink {
    fn emit(&self, _: Value) {}
}

#[derive(Debug, Clone)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Value,
}

#[derive(Debug, Clone, Default)]
pub struct AssistantTurn {
    pub text: Option<String>,
    pub reasoning: Option<String>,
    pub tool_calls: Vec<ToolCall>,
    pub finish_reason: Option<String>,
    pub usage: Option<Value>,
}

#[derive(Debug, Clone)]
pub enum RuntimeEvent {
    TurnStart {
        input: Value,
        source: Option<Value>,
        run_id: Option<String>,
    },
    AssistantDelta {
        text: String,
    },
    ReasoningDelta {
        text: String,
    },
    AssistantMessage {
        text: Option<String>,
        tool_calls: Vec<String>,
        reasoning: Option<String>,
        usage: Option<Value>,
    },
    ToolProposed {
        tool_call_id: String,
        name: String,
        arguments: Value,
        risk_level: Option<String>,
    },
    ToolStarted {
        tool_call_id: String,
        name: String,
    },
    ToolFinished {
        tool_call_id: String,
        name: String,
        result: Value,
        error: Option<String>,
    },
    PermissionRequired {
        tool_call_id: String,
        name: String,
        arguments: Value,
        reason: String,
    },
    IterationEnd {
        iteration: usize,
    },
    TurnEnd {
        status: String,
        iterations: usize,
    },
    Error {
        error: String,
        error_type: String,
    },
    Interrupted {
        iterations: usize,
    },
    Compacting,
    Compacted {
        text: String,
    },
    ModelChanged {
        model: String,
    },
}

/// The only event envelope exposed by the Rust Runtime to product surfaces.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RuntimeEventEnvelopeV1 {
    #[serde(rename = "type")]
    pub event_type: String,
    pub version: u8,
    #[serde(rename = "sessionId")]
    pub session_id: String,
    pub sequence: u64,
    pub payload: Value,
}

/// Authoritative lifecycle state for one session runtime.
///
/// A [`RuntimeHandle`] owns this state independently from the worker thread so
/// control commands never need to lock the agent loop itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeState {
    Idle,
    Running,
    WaitingApproval,
    WaitingUser,
    Cancelling,
    Interrupted,
    Failed,
    Completed,
}

impl RuntimeState {
    pub fn is_active(self) -> bool {
        matches!(
            self,
            Self::Running | Self::WaitingApproval | Self::WaitingUser | Self::Cancelling
        )
    }
}

impl RuntimeEvent {
    pub fn to_frame(&self, session_id: &str, sequence: u64) -> Value {
        let (event_type, payload) = match self {
            Self::TurnStart {
                input,
                source,
                run_id,
            } => (
                "turn_start",
                json!({"input": input, "source": source, "run_id": run_id}),
            ),
            Self::AssistantDelta { text } => ("assistant_delta", json!({"text": text})),
            Self::ReasoningDelta { text } => ("reasoning_delta", json!({"text": text})),
            Self::AssistantMessage {
                text,
                tool_calls,
                reasoning,
                usage,
            } => (
                "assistant_message",
                json!({"text": text, "tool_calls": tool_calls, "reasoning": reasoning, "usage": usage}),
            ),
            Self::ToolProposed {
                tool_call_id,
                name,
                arguments,
                risk_level,
            } => (
                "tool_proposed",
                json!({"tool_call_id": tool_call_id, "name": name, "arguments": arguments, "risk_level": risk_level}),
            ),
            Self::ToolStarted { tool_call_id, name } => (
                "tool_started",
                json!({"tool_call_id": tool_call_id, "name": name}),
            ),
            Self::ToolFinished {
                tool_call_id,
                name,
                result,
                error,
            } => (
                "tool_finished",
                json!({"tool_call_id": tool_call_id, "name": name, "result": result, "error": error}),
            ),
            Self::PermissionRequired {
                tool_call_id,
                name,
                arguments,
                reason,
            } => (
                "permission_required",
                json!({"tool_call_id": tool_call_id, "name": name, "arguments": arguments, "reason": reason}),
            ),
            Self::IterationEnd { iteration } => ("iteration_end", json!({"iteration": iteration})),
            Self::TurnEnd { status, iterations } => (
                "turn_end",
                json!({"status": status, "iterations": iterations}),
            ),
            Self::Error { error, error_type } => {
                ("error", json!({"error": error, "error_type": error_type}))
            }
            Self::Interrupted { iterations } => ("interrupted", json!({"iterations": iterations})),
            Self::Compacting => ("compacting", json!({})),
            Self::Compacted { text } => ("compacted", json!({"text": text})),
            Self::ModelChanged { model } => ("model_changed", json!({"model": model})),
        };
        serde_json::to_value(RuntimeEventEnvelopeV1 {
            event_type: event_type.to_string(),
            version: 1,
            session_id: session_id.to_string(),
            sequence,
            payload,
        })
        .expect("runtime event envelope is serializable")
    }
}

pub trait ToolExecutor: Send + Sync {
    fn execute(&self, call: &ToolCall) -> ToolResult;
}

/// A worker-created file that is not visible as a product artifact until the
/// Rust Runtime validates, promotes, hashes and registers it.
#[derive(Debug, Clone)]
pub struct StagedArtifact {
    pub staging_path: PathBuf,
    pub relative_path: String,
    pub kind: String,
    pub incomplete: bool,
}

#[derive(Debug, Clone)]
pub struct ToolResult {
    pub tool_call_id: String,
    pub output: Value,
    pub error: Option<String>,
    pub staged_artifacts: Vec<StagedArtifact>,
    pub validation_criteria: Option<Value>,
}

impl ToolResult {
    pub fn success(tool_call_id: &str, output: Value) -> Self {
        Self {
            tool_call_id: tool_call_id.to_string(),
            output,
            error: None,
            staged_artifacts: Vec::new(),
            validation_criteria: None,
        }
    }

    pub fn failure(tool_call_id: &str, error: impl Into<String>) -> Self {
        let error = error.into();
        Self {
            tool_call_id: tool_call_id.to_string(),
            output: json!({"ok": false, "error": error}),
            error: Some(error),
            staged_artifacts: Vec::new(),
            validation_criteria: None,
        }
    }
}

struct UnavailableToolExecutor;
impl ToolExecutor for UnavailableToolExecutor {
    fn execute(&self, call: &ToolCall) -> ToolResult {
        ToolResult::failure(
            &call.id,
            format!("capability is not registered: {}", call.name),
        )
    }
}

/// Shared Rust authorities used by every active session. The contained SQLite
/// writers are serialized per authority, while provider streaming and runtime
/// controls remain independent.
#[derive(Clone)]
pub struct RuntimeAuthorities {
    ledger: Arc<Mutex<LedgerWriter>>,
    idempotency: Arc<Mutex<IdempotencyWriter>>,
    approvals: Arc<Mutex<ApprovalWriter>>,
}

impl RuntimeAuthorities {
    pub fn open(state_dir: impl AsRef<Path>) -> Result<Self, String> {
        let state_dir = state_dir.as_ref();
        fs::create_dir_all(state_dir).map_err(|error| error.to_string())?;
        Ok(Self {
            ledger: Arc::new(Mutex::new(
                LedgerWriter::open(state_dir.join("run_events.db"))
                    .map_err(|error| error.to_string())?,
            )),
            idempotency: Arc::new(Mutex::new(
                IdempotencyWriter::open(state_dir.join("side_effects.db"))
                    .map_err(|error| error.to_string())?,
            )),
            approvals: Arc::new(Mutex::new(
                ApprovalWriter::open(state_dir.join("audit_events.db").to_string_lossy().as_ref())
                    .map_err(|error| error.to_string())?,
            )),
        })
    }

    pub fn ledger(&self) -> Arc<Mutex<LedgerWriter>> {
        self.ledger.clone()
    }
}

#[derive(Clone)]
pub struct RuntimeConfig {
    /// Product-facing routed id (for example `anthropic:claude-sonnet-4-6`).
    /// `model` below is the provider-facing bare id.
    pub model_id: String,
    pub model: String,
    pub protocol: String,
    pub api_key: String,
    pub base_url: String,
    pub max_iterations: usize,
    pub max_retries: u32,
    pub ttft_timeout: Option<f64>,
    pub tool_timeout: Option<f64>,
    pub model_settings: Value,
    pub system_prompt: Option<String>,
    pub workspace: Option<String>,
    /// Unattended runs park approval requests in the Inbox authority instead
    /// of waiting on an in-composer response.
    pub unattended: bool,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            model_id: String::new(),
            model: String::new(),
            protocol: "openai_chat".to_string(),
            api_key: String::new(),
            base_url: "https://api.openai.com/v1".to_string(),
            max_iterations: DEFAULT_MAX_ITERATIONS,
            max_retries: 2,
            ttft_timeout: None,
            tool_timeout: None,
            model_settings: json!({}),
            system_prompt: None,
            workspace: None,
            unattended: false,
        }
    }
}

pub struct RuntimeHost {
    config: RuntimeConfig,
    messages: Vec<Value>,
    cancel: Arc<AtomicBool>,
    provider_cancel: Arc<AtomicBool>,
    steering: Arc<SteeringQueue>,
    sequence: Arc<Mutex<u64>>,
    session_id: String,
    authorities: Option<RuntimeAuthorities>,
    tools: Option<Value>,
    tool_executor: Arc<dyn ToolExecutor>,
    approvals: Arc<ApprovalController>,
    runtime_state: Option<Arc<Mutex<RuntimeState>>>,
    run_id: Option<String>,
    sink: Arc<dyn EventSink>,
}

/// A queued steering/follow-up instruction: (text, optional MessageSource sidecar).
type SteeringItem = (String, Option<Value>);
type SteeringQueue = Mutex<Vec<SteeringItem>>;

#[derive(Debug)]
struct QueuedRun {
    input: String,
    source: Option<Value>,
    run_id: String,
}

enum RuntimeOperation {
    Run(QueuedRun),
    Resume { run_id: String },
    Retry { run_id: String },
}

enum RuntimeCommand {
    Execute(RuntimeOperation),
    SwitchModel {
        change: ModelChange,
        reply: mpsc::Sender<Result<Option<String>, String>>,
    },
    Truncate {
        index: usize,
        reply: mpsc::Sender<Result<usize, String>>,
    },
}

enum ModelChange {
    LegacyId(String),
    Resolved(Box<RuntimeConfig>),
}

/// Cloneable control surface for a session-owned background runtime.
///
/// The worker thread has exclusive ownership of [`RuntimeHost`]. The handle
/// exposes only short-lived state locks, cooperative cancellation tokens and
/// bounded queues, so steering/cancel/follow-up remain responsive while a
/// provider request or capability is in flight.
pub struct RuntimeHandle {
    session_id: String,
    command_tx: mpsc::Sender<RuntimeCommand>,
    cancel: Arc<AtomicBool>,
    provider_cancel: Arc<AtomicBool>,
    steering: Arc<SteeringQueue>,
    follow_ups: Arc<Mutex<VecDeque<QueuedRun>>>,
    state: Arc<Mutex<RuntimeState>>,
    messages: Arc<RwLock<Vec<Value>>>,
    approvals: Arc<ApprovalController>,
}

fn now_ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn uuid_v4() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn is_retryable_error(e: &str) -> bool {
    let lower = e.to_lowercase();
    lower.contains("429")
        || lower.contains("502")
        || lower.contains("503")
        || lower.contains("504")
        || lower.contains("connection")
        || lower.contains("timeout")
        || lower.contains("transport")
}

fn classify_transient_error(e: &str) -> String {
    let lower = e.to_lowercase();
    if lower.contains("429") || lower.contains("rate") {
        "RateLimit".to_string()
    } else if lower.contains("50") || lower.contains("server") {
        "ServerError".to_string()
    } else if lower.contains("timeout") || lower.contains("ttft") {
        "TTFTTimeout".to_string()
    } else if lower.contains("connection") || lower.contains("transport") {
        "ConnectionError".to_string()
    } else {
        "Unknown".to_string()
    }
}

#[derive(Clone)]
struct RuntimeEventEmitter {
    session_id: String,
    sequence: Arc<Mutex<u64>>,
    sink: Arc<dyn EventSink>,
}

impl RuntimeEventEmitter {
    fn emit(&self, event: RuntimeEvent) {
        let mut sequence = self.sequence.lock().unwrap();
        *sequence += 1;
        self.sink.emit(event.to_frame(&self.session_id, *sequence));
    }
}

/// Provider-private stream frames terminate here. This adapter is the only
/// conversion boundary from provider deltas to the product event protocol;
/// raw provider frames never reach a Tauri/stdout event sink.
struct ProviderEventWriter {
    emitter: RuntimeEventEmitter,
    buffer: Vec<u8>,
    text: String,
    reasoning: String,
}

impl ProviderEventWriter {
    fn new(emitter: RuntimeEventEmitter) -> Self {
        Self {
            emitter,
            buffer: Vec::new(),
            text: String::new(),
            reasoning: String::new(),
        }
    }

    fn process_line(&mut self, line: &[u8]) {
        let Ok(frame) = serde_json::from_slice::<Value>(line) else {
            return;
        };
        let Some(data) = frame.get("data") else {
            return;
        };
        if let Some(text) = data.get("text_delta").and_then(Value::as_str) {
            self.text.push_str(text);
            self.emitter.emit(RuntimeEvent::AssistantDelta {
                text: text.to_string(),
            });
        }
        if let Some(text) = data.get("reasoning_delta").and_then(Value::as_str) {
            self.reasoning.push_str(text);
            self.emitter.emit(RuntimeEvent::ReasoningDelta {
                text: text.to_string(),
            });
        }
    }

    fn process_complete_lines(&mut self) {
        while let Some(newline) = self.buffer.iter().position(|byte| *byte == b'\n') {
            let mut line: Vec<u8> = self.buffer.drain(..=newline).collect();
            while matches!(line.last(), Some(b'\n' | b'\r')) {
                line.pop();
            }
            if !line.is_empty() {
                self.process_line(&line);
            }
        }
    }

    fn finish(mut self) -> (String, String) {
        self.process_complete_lines();
        if !self.buffer.is_empty() {
            let tail = std::mem::take(&mut self.buffer);
            self.process_line(&tail);
        }
        (self.text, self.reasoning)
    }
}

impl Write for ProviderEventWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.buffer.extend_from_slice(buf);
        self.process_complete_lines();
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.process_complete_lines();
        Ok(())
    }
}

fn validate_schema_value(value: &Value, schema: &Value, path: &str) -> Result<(), String> {
    if let Some(allowed) = schema.get("enum").and_then(Value::as_array) {
        if !allowed.iter().any(|candidate| candidate == value) {
            return Err(format!(
                "tool schema validation failed at {path}: value is not allowed"
            ));
        }
    }
    if let Some(expected) = schema.get("type").and_then(Value::as_str) {
        let matches = match expected {
            "object" => value.is_object(),
            "array" => value.is_array(),
            "string" => value.is_string(),
            "number" => value.is_number(),
            "integer" => value.as_i64().is_some() || value.as_u64().is_some(),
            "boolean" => value.is_boolean(),
            "null" => value.is_null(),
            _ => false,
        };
        if !matches {
            return Err(format!(
                "tool schema validation failed at {path}: expected {expected}"
            ));
        }
    }
    if let Some(object) = value.as_object() {
        if let Some(required) = schema.get("required").and_then(Value::as_array) {
            for key in required.iter().filter_map(Value::as_str) {
                if !object.contains_key(key) {
                    return Err(format!(
                        "tool schema validation failed at {path}: missing {key}"
                    ));
                }
            }
        }
        if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
            for (key, item) in object {
                if let Some(property_schema) = properties.get(key) {
                    validate_schema_value(item, property_schema, &format!("{path}.{key}"))?;
                }
            }
        }
    }
    if let (Some(items), Some(item_schema)) = (
        value.as_array(),
        schema.get("items").filter(|item| item.is_object()),
    ) {
        for (index, item) in items.iter().enumerate() {
            validate_schema_value(item, item_schema, &format!("{path}[{index}]"))?;
        }
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProviderStreamOutcome {
    Completed,
    Interrupted,
}

impl RuntimeHost {
    pub fn new(session_id: &str, config: RuntimeConfig) -> Self {
        let mut messages = Vec::new();
        if let Some(ref sys) = config.system_prompt {
            messages.push(json!({"role": "system", "content": sys}));
        }
        Self {
            config,
            messages,
            cancel: Arc::new(AtomicBool::new(false)),
            provider_cancel: Arc::new(AtomicBool::new(false)),
            steering: Arc::new(Mutex::new(Vec::new())),
            sequence: Arc::new(Mutex::new(0)),
            session_id: session_id.to_string(),
            authorities: None,
            tools: None,
            tool_executor: Arc::new(UnavailableToolExecutor),
            approvals: Arc::new(ApprovalController::default()),
            runtime_state: None,
            run_id: None,
            sink: Arc::new(NullSink),
        }
    }

    pub fn with_tools(mut self, tools: Value) -> Self {
        self.tools = Some(tools);
        self
    }
    pub fn with_tool_executor(mut self, executor: Arc<dyn ToolExecutor>) -> Self {
        self.tool_executor = executor;
        self
    }
    pub fn with_authorities(mut self, authorities: RuntimeAuthorities) -> Self {
        self.authorities = Some(authorities);
        self
    }
    pub fn with_messages(mut self, messages: Vec<Value>) -> Self {
        self.messages = messages;
        self
    }
    pub fn with_run_id(mut self, run_id: String) -> Self {
        self.run_id = Some(run_id);
        self
    }

    pub fn set_run_id(&mut self, run_id: String) {
        self.run_id = Some(run_id);
    }

    /// Install a custom event sink (e.g. Tauri emit). Default is NullSink.
    pub fn with_event_sink(mut self, sink: Arc<dyn EventSink>) -> Self {
        self.sink = sink;
        self
    }

    pub fn reset_cancel(&self) {
        self.cancel.store(false, Ordering::SeqCst);
        self.provider_cancel.store(false, Ordering::SeqCst);
    }
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
        self.provider_cancel.store(true, Ordering::SeqCst);
    }
    pub fn steer(&self, text: &str, source: Option<Value>) {
        self.steering
            .lock()
            .unwrap()
            .push((text.to_string(), source));
        self.provider_cancel.store(true, Ordering::SeqCst);
    }
    pub fn model(&self) -> &str {
        if self.config.model_id.is_empty() {
            &self.config.model
        } else {
            &self.config.model_id
        }
    }
    pub fn messages(&self) -> &[Value] {
        &self.messages
    }
    pub fn truncate_messages(&mut self, index: usize) {
        if index < self.messages.len() {
            self.messages.truncate(index);
        }
    }

    pub fn switch_model(&mut self, model: &str) -> Option<String> {
        if model.is_empty() || model == self.model() {
            return None;
        }
        let had_history = self
            .messages
            .iter()
            .any(|m| m.get("role").and_then(|r| r.as_str()) != Some("system"));
        self.config.model = model.to_string();
        self.config.model_id = model.to_string();
        if had_history {
            let notice = format!("Model switched to {model}");
            self.messages.push(json!({
                "role": "notice", "kind": "model_switch", "text": &notice, "model": model, "ts": now_ts()
            }));
            Some(notice)
        } else {
            None
        }
    }

    pub fn switch_runtime_config(&mut self, config: RuntimeConfig) -> Option<String> {
        let model_id = if config.model_id.is_empty() {
            config.model.clone()
        } else {
            config.model_id.clone()
        };
        if model_id.is_empty() {
            return None;
        }
        let changed = model_id != self.model();
        let had_history = self
            .messages
            .iter()
            .any(|message| message.get("role").and_then(Value::as_str) != Some("system"));
        self.config.model_id = model_id.clone();
        self.config.model = config.model;
        self.config.protocol = config.protocol;
        self.config.api_key = config.api_key;
        self.config.base_url = config.base_url;
        self.config.model_settings = config.model_settings;
        self.config.max_iterations = config.max_iterations;
        self.config.max_retries = config.max_retries;
        self.config.ttft_timeout = config.ttft_timeout;
        self.config.tool_timeout = config.tool_timeout;
        self.config.workspace = config.workspace;
        self.config.unattended = config.unattended;
        if self.config.system_prompt != config.system_prompt {
            self.config.system_prompt = config.system_prompt.clone();
            if let Some(prompt) = config.system_prompt {
                if let Some(system) = self
                    .messages
                    .iter_mut()
                    .find(|message| message.get("role").and_then(Value::as_str) == Some("system"))
                {
                    *system = json!({"role": "system", "content": prompt});
                } else {
                    self.messages
                        .insert(0, json!({"role": "system", "content": prompt}));
                }
            } else {
                self.messages.retain(|message| {
                    message.get("role").and_then(Value::as_str) != Some("system")
                });
            }
        }
        if changed && had_history {
            let notice = format!("Model switched to {model_id}");
            self.messages.push(json!({
                "role": "notice", "kind": "model_switch", "text": &notice,
                "model": model_id, "ts": now_ts()
            }));
            self.emit_event(RuntimeEvent::ModelChanged { model: model_id });
            Some(notice)
        } else {
            None
        }
    }

    fn emit_event(&self, event: RuntimeEvent) {
        self.event_emitter().emit(event);
    }

    fn event_emitter(&self) -> RuntimeEventEmitter {
        RuntimeEventEmitter {
            session_id: self.session_id.clone(),
            sequence: self.sequence.clone(),
            sink: self.sink.clone(),
        }
    }

    fn outbound_messages(&self) -> Vec<Value> {
        let sidecars = ["source", "_display", "ts", "reasoning", "usage"];
        self.messages
            .iter()
            .filter(|m| m.get("role").and_then(|r| r.as_str()) != Some("notice"))
            .map(|m| {
                let has_sidecar = sidecars.iter().any(|s| m.get(*s).is_some());
                if has_sidecar {
                    let mut out = serde_json::Map::new();
                    if let Some(obj) = m.as_object() {
                        for (k, v) in obj {
                            if !sidecars.contains(&k.as_str()) {
                                out.insert(k.clone(), v.clone());
                            }
                        }
                    }
                    Value::Object(out)
                } else {
                    m.clone()
                }
            })
            .collect()
    }

    fn build_request(&self) -> ProviderRequest {
        ProviderRequest {
            protocol: self.config.protocol.clone(),
            model: self.config.model.clone(),
            messages: Value::Array(self.outbound_messages()),
            tools: self.tools.clone(),
            settings: Some(self.config.model_settings.clone()),
            api_key: self.config.api_key.clone(),
            base_url: self.config.base_url.clone(),
        }
    }

    fn ledger_transition(
        &self,
        event_type: &str,
        actor: &str,
        payload: Value,
    ) -> Result<(), String> {
        if let Some(authorities) = &self.authorities {
            let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
            let ws = self.config.workspace.clone().unwrap_or_default();
            authorities
                .ledger
                .lock()
                .unwrap()
                .transition(&run_id, event_type, actor, now_ts(), &payload, &ws)
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }

    fn ledger_append(&self, event_type: &str, actor: &str, payload: Value) -> Result<(), String> {
        if let Some(authorities) = &self.authorities {
            let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
            let workspace = self.config.workspace.clone().unwrap_or_default();
            authorities
                .ledger
                .lock()
                .unwrap()
                .append(&run_id, event_type, actor, now_ts(), &payload, &workspace)
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }

    fn finish_run_ledger(&self, result: &Result<String, String>) -> Result<(), String> {
        match result {
            Ok(status) if status == "completed" => {
                self.ledger_transition("run.completed", "system", json!({"kind": "run"}))
            }
            Ok(status) if status == "interrupted" => {
                self.ledger_transition("run.interrupted", "system", json!({"kind": "run"}))
            }
            Ok(status) => self.ledger_transition(
                "run.failed",
                "system",
                json!({"kind": "run", "reason": status}),
            ),
            Err(error) => self.ledger_transition(
                "run.failed",
                "system",
                json!({"reason": error, "kind": "run"}),
            ),
        }
    }

    fn assistant_message(
        &self,
        text: Option<&str>,
        reasoning: Option<&str>,
        tool_calls: &[ToolCall],
    ) -> Value {
        let mut msg = json!({"role": "assistant", "ts": now_ts(), "model": self.config.model});
        if let Some(t) = text {
            msg["content"] = Value::String(t.to_string());
        }
        if let Some(r) = reasoning {
            msg["reasoning"] = Value::String(r.to_string());
        }
        if !tool_calls.is_empty() {
            let tc_json: Vec<Value> = tool_calls
                .iter()
                .map(|tc| {
                    json!({
                        "id": &tc.id,
                        "type": "function",
                        "function": {
                            "name": &tc.name,
                            "arguments": serde_json::to_string(&tc.arguments).unwrap_or_default(),
                        }
                    })
                })
                .collect();
            msg["tool_calls"] = Value::Array(tc_json);
        }
        msg
    }

    fn drain_steering(&mut self) -> bool {
        let mut q = self.steering.lock().unwrap();
        if q.is_empty() {
            return false;
        }
        for (text, source) in q.iter() {
            let mut message = json!({"role": "user", "content": text, "ts": now_ts()});
            if let Some(src) = source {
                message["source"] = src.clone();
            }
            self.messages.push(message);
        }
        q.clear();
        true
    }

    fn stream_provider(
        &self,
        req: &ProviderRequest,
        stream_id: &str,
        turn: &mut Option<AssistantTurn>,
        streamed_text: &mut Vec<String>,
        streamed_reasoning: &mut Vec<String>,
    ) -> Result<ProviderStreamOutcome, String> {
        let mut writer = ProviderEventWriter::new(self.event_emitter());
        let result = provider::stream(req, &mut writer, stream_id, &self.provider_cancel);
        let (partial_text, partial_reasoning) = writer.finish();
        if !partial_text.is_empty() {
            streamed_text.push(partial_text);
        }
        if !partial_reasoning.is_empty() {
            streamed_reasoning.push(partial_reasoning);
        }
        let result = result?;
        if result
            .get("cancelled")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            return Ok(ProviderStreamOutcome::Interrupted);
        }
        let text = result
            .get("text")
            .and_then(|t| t.as_str())
            .map(String::from);
        let reasoning = result
            .get("reasoning")
            .and_then(|r| r.as_str())
            .map(String::from);
        let finish_reason = result
            .get("finish_reason")
            .and_then(|f| f.as_str())
            .map(String::from);
        let usage = result.get("usage").cloned();
        let tool_calls: Vec<ToolCall> = result
            .get("tool_calls")
            .and_then(|tcs| tcs.as_array())
            .map(|tcs| {
                tcs.iter()
                    .map(|tc| ToolCall {
                        id: tc
                            .get("id")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        name: tc
                            .get("name")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        arguments: tc.get("arguments").cloned().unwrap_or(json!({})),
                    })
                    .collect()
            })
            .unwrap_or_default();
        *turn = Some(AssistantTurn {
            text,
            reasoning,
            tool_calls,
            finish_reason,
            usage,
        });
        Ok(ProviderStreamOutcome::Completed)
    }

    fn tool_contract(&self, call: &ToolCall) -> Result<(Value, Option<ToolMetadata>), String> {
        let definitions = self
            .tools
            .as_ref()
            .and_then(Value::as_array)
            .ok_or_else(|| "no capability contracts are registered".to_string())?;
        let definition = definitions
            .iter()
            .find(|definition| {
                definition.get("name").and_then(Value::as_str) == Some(call.name.as_str())
                    || definition
                        .get("function")
                        .and_then(|function| function.get("name"))
                        .and_then(Value::as_str)
                        == Some(call.name.as_str())
            })
            .ok_or_else(|| format!("capability contract not found: {}", call.name))?;
        let function = definition.get("function").unwrap_or(definition);
        let schema = function
            .get("parameters")
            .or_else(|| definition.get("parameters"))
            .cloned()
            .unwrap_or_else(|| json!({"type": "object"}));
        validate_schema_value(&call.arguments, &schema, "arguments")?;
        let metadata = [
            definition.get("metadata"),
            definition.get("x-delta"),
            definition.get("x_delta"),
            function.get("metadata"),
            function.get("x-delta"),
            function.get("x_delta"),
        ]
        .into_iter()
        .flatten()
        .find_map(|value| serde_json::from_value::<ToolMetadata>(value.clone()).ok());
        Ok((schema, metadata))
    }

    fn policy_for(
        &self,
        call: &ToolCall,
        metadata: Option<ToolMetadata>,
    ) -> Result<(RiskLevel, Decision), String> {
        let level = policy::classify(&call.name, Some(&call.arguments), metadata.as_ref());
        let explicitly_gated = metadata
            .as_ref()
            .and_then(|item| item.requires_approval)
            .unwrap_or(true);
        let auto_allowed = level <= RiskLevel::L1 && !explicitly_gated;
        let workspace = self.config.workspace.clone().unwrap_or_default();
        let roots = if workspace.is_empty() {
            Vec::new()
        } else {
            vec![RootEntry {
                path: workspace.clone(),
                writable: true,
            }]
        };
        let evaluated = policy::evaluate(PolicyEvaluateInput {
            tool_name: call.name.clone(),
            arguments: Some(call.arguments.clone()),
            metadata,
            decision: Decision {
                allowed: auto_allowed,
                reason: if auto_allowed {
                    "auto-approved by Rust policy".to_string()
                } else {
                    format!("explicit approval required for {level:?}")
                },
                needs_user: !auto_allowed,
                rule: if auto_allowed {
                    "runtime.auto_low_risk".to_string()
                } else {
                    String::new()
                },
                grant: if auto_allowed {
                    "policy".to_string()
                } else {
                    String::new()
                },
            },
            level: level as i64,
            workspace_root: workspace,
            roots,
        })
        .map_err(|error| error.to_string())?;
        let evaluated_level = RiskLevel::from_i64(evaluated.level)
            .ok_or_else(|| "policy returned an invalid risk level".to_string())?;
        Ok((evaluated_level, evaluated.decision))
    }

    fn set_runtime_state(&self, state: RuntimeState) {
        if let Some(runtime_state) = &self.runtime_state {
            *runtime_state.lock().unwrap() = state;
        }
    }

    fn record_approval(
        &self,
        call: &ToolCall,
        stage: &str,
        status: &str,
        approval: Option<&str>,
        reason: &str,
        level: RiskLevel,
    ) -> Result<(), String> {
        let Some(authorities) = &self.authorities else {
            return Ok(());
        };
        let workspace = self.config.workspace.clone().unwrap_or_default();
        authorities
            .approvals
            .lock()
            .unwrap()
            .record(
                ApprovalRecordInput {
                    session_id: self.session_id.clone(),
                    agent: Some("delta".to_string()),
                    workspace: Some(workspace.clone()),
                    connector: None,
                    tool: call.name.clone(),
                    stage: stage.to_string(),
                    status: Some(status.to_string()),
                    approval: approval.map(str::to_string),
                    arguments: Some(call.arguments.clone()),
                    result_preview: None,
                    reason: Some(reason.to_string()),
                    resource: None,
                    level: Some(format!("{level:?}")),
                    isolation: Some("runtime".to_string()),
                    ts: Some(now_ts()),
                },
                now_ts(),
                &workspace,
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn register_checkpoint(
        &self,
        phase: &str,
        pending_tool_call: Option<Value>,
        artifacts: Vec<Value>,
        error: Option<String>,
    ) -> Result<(), String> {
        let Some(authorities) = &self.authorities else {
            return Ok(());
        };
        let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
        let workspace = self.config.workspace.clone().unwrap_or_default();
        let ledger = authorities.ledger.lock().unwrap();
        CheckpointWriter::new(&ledger)
            .register(
                CheckpointRegisterInput {
                    checkpoint_id: None,
                    run_id,
                    session_id: self.session_id.clone(),
                    phase: phase.to_string(),
                    pending_tool_call,
                    pending_inbox_item_id: None,
                    last_event_seq: Some(*self.sequence.lock().unwrap() as i64),
                    todo_summary: Vec::new(),
                    recent_artifacts: artifacts,
                    error,
                },
                now_ts(),
                &workspace,
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn require_approval(
        &self,
        call: &ToolCall,
        reason: &str,
        level: RiskLevel,
    ) -> Result<ApprovalDecision, String> {
        self.emit_event(RuntimeEvent::PermissionRequired {
            tool_call_id: call.id.clone(),
            name: call.name.clone(),
            arguments: call.arguments.clone(),
            reason: reason.to_string(),
        });
        self.ledger_append(
            "approval.required",
            "runtime",
            json!({"tool_call_id": call.id, "tool": call.name, "reason": reason, "level": format!("{level:?}")}),
        )?;
        self.record_approval(call, "approval_required", "pending", None, reason, level)?;
        self.register_checkpoint(
            "awaiting_approval",
            Some(json!({"id": call.id, "name": call.name, "arguments": call.arguments})),
            Vec::new(),
            None,
        )?;
        if self.config.unattended {
            self.ledger_append(
                "approval.inbox",
                "runtime",
                json!({"tool_call_id": call.id, "tool": call.name}),
            )?;
            self.record_approval(call, "approval_parked", "inbox", None, reason, level)?;
        }

        let receiver = self.approvals.begin(&call.id)?;
        self.set_runtime_state(if self.config.unattended {
            RuntimeState::WaitingUser
        } else {
            RuntimeState::WaitingApproval
        });
        loop {
            if self.cancel.load(Ordering::Acquire) {
                self.approvals.cancel(&call.id);
                self.record_approval(
                    call,
                    "approval_resolved",
                    "cancelled",
                    None,
                    "run cancelled while approval was pending",
                    level,
                )?;
                self.ledger_append(
                    "approval.cancelled",
                    "user",
                    json!({"tool_call_id": call.id, "tool": call.name}),
                )?;
                return Err("run cancelled while approval was pending".to_string());
            }
            match receiver.recv_timeout(Duration::from_millis(25)) {
                Ok(decision) => {
                    self.set_runtime_state(RuntimeState::Running);
                    let status = if decision.is_approved() {
                        "approved"
                    } else {
                        "denied"
                    };
                    self.record_approval(
                        call,
                        "approval_resolved",
                        status,
                        Some(decision.as_str()),
                        reason,
                        level,
                    )?;
                    self.ledger_append(
                        if decision.is_approved() {
                            "approval.approved"
                        } else {
                            "approval.denied"
                        },
                        "user",
                        json!({"tool_call_id": call.id, "tool": call.name, "decision": decision.as_str()}),
                    )?;
                    if decision.is_approved() {
                        self.approvals
                            .remember(decision, &call.name, &call.arguments);
                    }
                    return Ok(decision);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return Err("approval request was abandoned".to_string());
                }
            }
        }
    }

    fn formalize_artifacts(&self, staged: &[StagedArtifact]) -> Result<Vec<Value>, String> {
        if staged.is_empty() {
            return Ok(Vec::new());
        }
        let authorities = self
            .authorities
            .as_ref()
            .ok_or_else(|| "artifact authority is unavailable".to_string())?;
        let workspace_text = self
            .config
            .workspace
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| "workspace is required for artifacts".to_string())?;
        let workspace = PathBuf::from(workspace_text)
            .canonicalize()
            .map_err(|error| format!("workspace is unavailable: {error}"))?;
        let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
        let staging_root = workspace.join(".delta").join("staging").join(&run_id);
        let mut formalized = Vec::new();

        for candidate in staged {
            let source = candidate
                .staging_path
                .canonicalize()
                .map_err(|error| format!("staged artifact is unavailable: {error}"))?;
            let canonical_staging = staging_root
                .canonicalize()
                .map_err(|error| format!("staging root is unavailable: {error}"))?;
            if !source.starts_with(&canonical_staging) || !source.is_file() {
                return Err("worker artifact escaped its staging root".to_string());
            }
            let relative = Path::new(&candidate.relative_path);
            if relative.is_absolute()
                || relative.components().any(|component| {
                    matches!(
                        component,
                        Component::ParentDir | Component::RootDir | Component::Prefix(_)
                    )
                })
            {
                return Err("artifact destination must be workspace-relative".to_string());
            }
            let target = workspace.join(relative);
            if !target.starts_with(&workspace) || target.starts_with(workspace.join(".delta")) {
                return Err("artifact destination is reserved or outside workspace".to_string());
            }
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            if target.exists() {
                fs::remove_file(&target).map_err(|error| error.to_string())?;
            }
            if fs::rename(&source, &target).is_err() {
                fs::copy(&source, &target).map_err(|error| error.to_string())?;
                fs::remove_file(&source).map_err(|error| error.to_string())?;
            }
            let bytes = fs::read(&target).map_err(|error| error.to_string())?;
            let sha256 = format!("{:x}", Sha256::digest(&bytes));
            let relative_path = candidate.relative_path.replace('\\', "/");
            let artifact = ArtifactInput {
                path: relative_path.clone(),
                name: target
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or(&relative_path)
                    .to_string(),
                kind: candidate.kind.clone(),
                size: bytes.len() as i64,
                modified_at: now_ts(),
                run_id: run_id.clone(),
                sha256: sha256.clone(),
                incomplete: candidate.incomplete,
                registered_at: now_ts(),
            };
            ArtifactRegistryWriter::new(&authorities.ledger.lock().unwrap())
                .register(&artifact, now_ts(), workspace_text)
                .map_err(|error| error.to_string())?;
            formalized.push(artifact.registered_payload());
        }
        Ok(formalized)
    }

    fn validate_tool_artifacts(
        &self,
        artifacts: &[Value],
        criteria: Option<&Value>,
    ) -> Result<Value, String> {
        let Some(authorities) = &self.authorities else {
            if artifacts.is_empty() {
                return Ok(json!({"ok": true, "checks": []}));
            }
            return Err("validation authority is unavailable".to_string());
        };
        let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
        let workspace = self.config.workspace.clone().unwrap_or_default();
        let default_criteria = json!({
            "min_artifacts": 0,
            "max_artifacts": 50,
            "require_complete": true
        });
        let ledger = authorities.ledger.lock().unwrap();
        let (_, result) = ValidationWriter::new(&ledger)
            .evaluate_and_register(
                &run_id,
                criteria.unwrap_or(&default_criteria),
                artifacts,
                &workspace,
                None,
                now_ts(),
            )
            .map_err(|error| error.to_string())?;
        serde_json::to_value(result).map_err(|error| error.to_string())
    }

    fn execute_tool_call(&self, call: &ToolCall) -> ToolResult {
        let (_schema, metadata) = match self.tool_contract(call) {
            Ok(contract) => contract,
            Err(error) => {
                let _ = self.ledger_append(
                    "tool.failed",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name, "stage": "schema", "error": error}),
                );
                return ToolResult::failure(&call.id, error);
            }
        };
        let (level, decision) = match self.policy_for(call, metadata) {
            Ok(outcome) => outcome,
            Err(error) => return ToolResult::failure(&call.id, error),
        };
        self.emit_event(RuntimeEvent::ToolProposed {
            tool_call_id: call.id.clone(),
            name: call.name.clone(),
            arguments: call.arguments.clone(),
            risk_level: Some(format!("{level:?}")),
        });
        if let Err(error) = self.ledger_append(
            "tool.proposed",
            "model",
            json!({"tool_call_id": call.id, "tool": call.name, "arguments": call.arguments, "level": format!("{level:?}")}),
        ) {
            return ToolResult::failure(&call.id, error);
        }

        if !decision.allowed
            && self
                .approvals
                .has_standing_grant(&call.name, &call.arguments)
        {
            if let Err(error) = self.record_approval(
                call,
                "standing_policy_resolved",
                "auto_approved",
                Some("standing"),
                "approved by a session-scoped standing grant",
                level,
            ) {
                return ToolResult::failure(&call.id, error);
            }
        } else if !decision.allowed {
            if !decision.needs_user {
                let _ = self.record_approval(
                    call,
                    "policy_denied",
                    "denied",
                    None,
                    &decision.reason,
                    level,
                );
                let _ = self.ledger_append(
                    "tool.failed",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name, "stage": "policy", "error": &decision.reason}),
                );
                return ToolResult::failure(&call.id, decision.reason);
            }
            match self.require_approval(call, &decision.reason, level) {
                Ok(approval) if approval.is_approved() => {}
                Ok(_) => {
                    let _ = self.ledger_append(
                        "tool.cancelled",
                        "user",
                        json!({"tool_call_id": call.id, "tool": call.name, "reason": "denied"}),
                    );
                    return ToolResult::failure(&call.id, "tool call denied by user");
                }
                Err(error) => {
                    let _ = self.ledger_append(
                        "tool.cancelled",
                        "runtime",
                        json!({"tool_call_id": call.id, "tool": call.name, "reason": &error}),
                    );
                    return ToolResult::failure(&call.id, error);
                }
            }
        } else if let Err(error) = self.record_approval(
            call,
            "policy_resolved",
            "auto_approved",
            Some("policy"),
            &decision.reason,
            level,
        ) {
            return ToolResult::failure(&call.id, error);
        }
        if let Err(error) = self.ledger_append(
            "tool.approved",
            "runtime",
            json!({"tool_call_id": call.id, "tool": call.name, "level": format!("{level:?}")}),
        ) {
            return ToolResult::failure(&call.id, error);
        }

        let Some(authorities) = &self.authorities else {
            return ToolResult::failure(&call.id, "runtime authorities are unavailable");
        };
        let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
        let plan_input = ToolLifecyclePlanInput {
            db: "side_effects.db".to_string(),
            run_id: run_id.clone(),
            tool_call_id: call.id.clone(),
            tool_name: call.name.clone(),
            args: call.arguments.clone(),
        };
        let plan = match tool_lifecycle::plan(&authorities.idempotency.lock().unwrap(), &plan_input)
        {
            Ok(plan) => plan,
            Err(error) => return ToolResult::failure(&call.id, error.to_string()),
        };
        match plan.action {
            PlanAction::Replay => {
                let output = plan.result.unwrap_or_else(|| json!({"ok": true}));
                let _ = self.ledger_append(
                    "tool.replayed",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name}),
                );
                return ToolResult::success(&call.id, output);
            }
            PlanAction::Uncertain => {
                let _ = self.ledger_append(
                    "tool.uncertain",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name, "operation_id": plan.operation_id}),
                );
                return ToolResult::failure(
                    &call.id,
                    plan.error
                        .and_then(|value| {
                            value
                                .get("error")
                                .and_then(Value::as_str)
                                .map(str::to_string)
                        })
                        .unwrap_or_else(|| "previous tool result is uncertain".to_string()),
                );
            }
            PlanAction::Execute => {}
        }

        self.emit_event(RuntimeEvent::ToolStarted {
            tool_call_id: call.id.clone(),
            name: call.name.clone(),
        });
        if let Err(error) = self.ledger_append(
            "tool.started",
            "runtime",
            json!({"tool_call_id": call.id, "tool": call.name}),
        ) {
            return ToolResult::failure(&call.id, error);
        }
        let mut result = self.tool_executor.execute(call);
        if self.cancel.load(Ordering::Acquire) {
            let _ = authorities
                .idempotency
                .lock()
                .unwrap()
                .mark_uncertain(&run_id, &call.id);
            let _ = self.ledger_append(
                "tool.cancelled",
                "user",
                json!({"tool_call_id": call.id, "tool": call.name, "state": "uncertain"}),
            );
            return ToolResult::failure(&call.id, "tool cancelled; side effect is uncertain");
        }
        if let Some(error) = result.error.clone() {
            let _ = authorities
                .idempotency
                .lock()
                .unwrap()
                .mark_failed(&run_id, &call.id, &error);
            let _ = self.ledger_append(
                "tool.failed",
                "runtime",
                json!({"tool_call_id": call.id, "tool": call.name, "error": error}),
            );
            return result;
        }

        let artifacts = match self.formalize_artifacts(&result.staged_artifacts) {
            Ok(artifacts) => artifacts,
            Err(error) => {
                let _ = authorities
                    .idempotency
                    .lock()
                    .unwrap()
                    .mark_failed(&run_id, &call.id, &error);
                let _ = self.ledger_append(
                    "tool.failed",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name, "stage": "artifact", "error": &error}),
                );
                return ToolResult::failure(&call.id, error);
            }
        };
        let validation = match self
            .validate_tool_artifacts(&artifacts, result.validation_criteria.as_ref())
        {
            Ok(validation) if validation.get("ok").and_then(Value::as_bool) != Some(false) => {
                validation
            }
            Ok(validation) => {
                let error = "tool artifact validation failed".to_string();
                let _ = authorities
                    .idempotency
                    .lock()
                    .unwrap()
                    .mark_failed(&run_id, &call.id, &error);
                let _ = self.ledger_append(
                    "tool.failed",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name, "error": error, "validation": validation}),
                );
                return ToolResult::failure(&call.id, error);
            }
            Err(error) => {
                let _ = authorities
                    .idempotency
                    .lock()
                    .unwrap()
                    .mark_failed(&run_id, &call.id, &error);
                let _ = self.ledger_append(
                    "tool.failed",
                    "runtime",
                    json!({"tool_call_id": call.id, "tool": call.name, "stage": "validation", "error": &error}),
                );
                return ToolResult::failure(&call.id, error);
            }
        };
        if let Some(output) = result.output.as_object_mut() {
            if !artifacts.is_empty() {
                output.insert("artifacts".to_string(), Value::Array(artifacts.clone()));
            }
            output.insert("validation".to_string(), validation);
        }
        if let Err(error) = authorities.idempotency.lock().unwrap().commit(
            &run_id,
            &call.id,
            &call.name,
            &call.arguments,
            &result.output,
        ) {
            return ToolResult::failure(&call.id, error.to_string());
        }
        if let Err(error) = self.ledger_append(
            "tool.completed",
            "runtime",
            json!({"tool_call_id": call.id, "tool": call.name, "result": result.output}),
        ) {
            return ToolResult::failure(&call.id, error);
        }
        if let Err(error) = self.register_checkpoint("tool_completed", None, artifacts, None) {
            return ToolResult::failure(&call.id, error);
        }
        result
    }

    fn loop_turn(&mut self) -> Result<String, String> {
        let mut iterations = 0usize;
        let mut turn_retries = 0u32;
        loop {
            if iterations >= self.config.max_iterations {
                self.emit_event(RuntimeEvent::TurnEnd {
                    status: "max_iterations_exceeded".to_string(),
                    iterations,
                });
                return Ok("max_iterations_exceeded".to_string());
            }
            iterations += 1;
            if self.cancel.load(Ordering::Relaxed) {
                self.messages
                    .push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                self.emit_event(RuntimeEvent::Interrupted { iterations });
                return Ok("interrupted".to_string());
            }
            // Steering received before the request begins is incorporated in
            // this request; steering received after this reset flips the
            // provider-only token and interrupts the live stream.
            self.provider_cancel.store(false, Ordering::SeqCst);
            self.drain_steering();
            let req = self.build_request();
            let mut turn: Option<AssistantTurn> = None;
            let mut streamed_text: Vec<String> = Vec::new();
            let mut streamed_reasoning: Vec<String> = Vec::new();
            let stream_id = uuid_v4();
            match self.stream_provider(
                &req,
                &stream_id,
                &mut turn,
                &mut streamed_text,
                &mut streamed_reasoning,
            ) {
                Ok(ProviderStreamOutcome::Completed) => {}
                Ok(ProviderStreamOutcome::Interrupted) => {
                    if !streamed_text.is_empty() || !streamed_reasoning.is_empty() {
                        let partial_text =
                            (!streamed_text.is_empty()).then(|| streamed_text.join(""));
                        let partial_reasoning =
                            (!streamed_reasoning.is_empty()).then(|| streamed_reasoning.join(""));
                        let partial = self.assistant_message(
                            partial_text.as_deref(),
                            partial_reasoning.as_deref(),
                            &[],
                        );
                        self.messages.push(partial);
                    }
                    if self.cancel.load(Ordering::Relaxed) {
                        self.messages
                            .push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                        self.emit_event(RuntimeEvent::Interrupted { iterations });
                        return Ok("interrupted".to_string());
                    }
                    if self.drain_steering() {
                        continue;
                    }
                    continue;
                }
                Err(e) => {
                    if turn_retries < self.config.max_retries
                        && streamed_text.is_empty()
                        && streamed_reasoning.is_empty()
                        && !self.cancel.load(Ordering::Relaxed)
                        && is_retryable_error(&e)
                    {
                        turn_retries += 1;
                        self.messages.push(json!({
                            "role": "notice", "kind": "retrying",
                            "text": format!("Retrying (attempt {turn_retries}/{})", self.config.max_retries),
                            "ts": now_ts()
                        }));
                        self.emit_event(RuntimeEvent::Error {
                            error: "Transient model failure - retrying.".to_string(),
                            error_type: classify_transient_error(&e),
                        });
                        continue;
                    }
                    if !streamed_text.is_empty() || !streamed_reasoning.is_empty() {
                        self.messages.push(self.assistant_message(
                            Some(&streamed_text.join("")),
                            Some(&streamed_reasoning.join("")),
                            &[],
                        ));
                    }
                    self.messages.push(
                        json!({"role": "notice", "kind": "error", "text": &e, "ts": now_ts()}),
                    );
                    self.emit_event(RuntimeEvent::Error {
                        error: e.clone(),
                        error_type: classify_transient_error(&e),
                    });
                    return Err(e);
                }
            }
            if self.cancel.load(Ordering::Relaxed) && turn.is_none() {
                if !streamed_text.is_empty() || !streamed_reasoning.is_empty() {
                    self.messages.push(self.assistant_message(
                        Some(&streamed_text.join("")),
                        Some(&streamed_reasoning.join("")),
                        &[],
                    ));
                }
                self.messages
                    .push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                self.emit_event(RuntimeEvent::Interrupted { iterations });
                return Ok("interrupted".to_string());
            }
            let turn = turn.unwrap_or_default();
            self.messages.push(self.assistant_message(
                turn.text.as_deref(),
                turn.reasoning.as_deref(),
                &turn.tool_calls,
            ));
            let tool_call_names: Vec<String> =
                turn.tool_calls.iter().map(|tc| tc.name.clone()).collect();
            self.emit_event(RuntimeEvent::AssistantMessage {
                text: turn.text.clone(),
                tool_calls: tool_call_names,
                reasoning: turn.reasoning.clone(),
                usage: turn.usage.clone(),
            });
            if turn.tool_calls.is_empty() {
                if self.drain_steering() {
                    continue;
                }
                self.emit_event(RuntimeEvent::TurnEnd {
                    status: "completed".to_string(),
                    iterations,
                });
                return Ok("completed".to_string());
            }
            for tc in &turn.tool_calls {
                if self.cancel.load(Ordering::Relaxed) {
                    break;
                }
                let result = self.execute_tool_call(tc);
                self.messages.push(json!({
                    "role": "tool", "tool_call_id": &tc.id, "content": &result.output
                }));
                self.emit_event(RuntimeEvent::ToolFinished {
                    tool_call_id: tc.id.clone(),
                    name: tc.name.clone(),
                    result: result.output.clone(),
                    error: result.error.clone(),
                });
            }
            self.emit_event(RuntimeEvent::IterationEnd {
                iteration: iterations,
            });
            if self.cancel.load(Ordering::Relaxed) {
                self.messages
                    .push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                self.emit_event(RuntimeEvent::Interrupted { iterations });
                return Ok("interrupted".to_string());
            }
            self.drain_steering();
        }
    }

    pub fn run(&mut self, user_input: &str, source: Option<Value>) -> Result<Value, String> {
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String(user_input.to_string()),
            source: source.clone(),
            run_id: self.run_id.clone(),
        });
        let mut message = json!({"role": "user", "content": user_input, "ts": now_ts()});
        if let Some(src) = &source {
            message["source"] = src.clone();
        }
        self.messages.push(message);
        self.ledger_transition("run.started", "user", json!({"kind": "run"}))?;
        let result = self.loop_turn();
        self.finish_run_ledger(&result)?;
        result.map(|s| json!({"status": s}))
    }

    pub fn resume(&mut self) -> Result<Value, String> {
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String("(resumed)".to_string()),
            source: None,
            run_id: self.run_id.clone(),
        });
        self.ledger_transition("run.started", "system", json!({"kind": "resume"}))?;
        let result = self.loop_turn();
        self.finish_run_ledger(&result)?;
        result.map(|status| json!({"status": status}))
    }

    pub fn retry(&mut self) -> Result<Value, String> {
        let tail_is_error = self.messages.iter().rev().any(|m| {
            m.get("role").and_then(|r| r.as_str()) == Some("notice")
                && m.get("kind").and_then(|k| k.as_str()) == Some("error")
        });
        if !tail_is_error {
            return Ok(json!({"status": "skipped"}));
        }
        if let Some(pos) = self.messages.iter().rposition(|m| {
            m.get("role").and_then(|r| r.as_str()) == Some("notice")
                && m.get("kind").and_then(|k| k.as_str()) == Some("error")
        }) {
            self.messages.remove(pos);
        }
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String(String::new()),
            source: None,
            run_id: self.run_id.clone(),
        });
        self.ledger_transition("run.started", "system", json!({"kind": "retry"}))?;
        let result = self.loop_turn();
        self.finish_run_ledger(&result)?;
        result.map(|status| json!({"status": status}))
    }
}

impl RuntimeHandle {
    /// Start a dedicated worker thread for `host`. The returned handle is idle;
    /// callers must register it before calling [`run`](Self::run).
    pub fn spawn(mut host: RuntimeHost) -> Result<Self, String> {
        let session_id = host.session_id.clone();
        let cancel = host.cancel.clone();
        let provider_cancel = host.provider_cancel.clone();
        let steering = host.steering.clone();
        let state = Arc::new(Mutex::new(RuntimeState::Idle));
        host.runtime_state = Some(state.clone());
        let approvals = host.approvals.clone();
        let follow_ups = Arc::new(Mutex::new(VecDeque::new()));
        let messages = Arc::new(RwLock::new(host.messages.clone()));
        let (command_tx, command_rx) = mpsc::channel();

        let worker_state = state.clone();
        let worker_follow_ups = follow_ups.clone();
        let worker_messages = messages.clone();
        let worker_cancel = cancel.clone();
        let worker_provider_cancel = provider_cancel.clone();
        std::thread::Builder::new()
            .name(format!("delta-runtime-{session_id}"))
            .spawn(move || {
                runtime_worker(
                    host,
                    command_rx,
                    worker_state,
                    worker_follow_ups,
                    worker_messages,
                    worker_cancel,
                    worker_provider_cancel,
                )
            })
            .map_err(|error| format!("failed to start runtime worker: {error}"))?;

        Ok(Self {
            session_id,
            command_tx,
            cancel,
            provider_cancel,
            steering,
            follow_ups,
            state,
            messages,
            approvals,
        })
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn state(&self) -> RuntimeState {
        *self.state.lock().unwrap()
    }

    pub fn messages(&self) -> Vec<Value> {
        self.messages.read().unwrap().clone()
    }

    pub fn resolve_approval(
        &self,
        tool_call_id: Option<&str>,
        decision: &str,
    ) -> Result<String, String> {
        let decision = ApprovalDecision::parse(decision)?;
        self.approvals.resolve(tool_call_id, decision)
    }

    pub fn pending_approvals(&self) -> Vec<String> {
        self.approvals.pending_ids()
    }

    pub fn run(&self, input: String, source: Option<Value>) -> Result<String, String> {
        let run_id = uuid_v4();
        self.enqueue_operation(RuntimeOperation::Run(QueuedRun {
            input,
            source,
            run_id: run_id.clone(),
        }))?;
        Ok(run_id)
    }

    pub fn resume(&self) -> Result<String, String> {
        let run_id = uuid_v4();
        self.enqueue_operation(RuntimeOperation::Resume {
            run_id: run_id.clone(),
        })?;
        Ok(run_id)
    }

    pub fn retry(&self) -> Result<String, String> {
        let run_id = uuid_v4();
        self.enqueue_operation(RuntimeOperation::Retry {
            run_id: run_id.clone(),
        })?;
        Ok(run_id)
    }

    fn enqueue_operation(&self, operation: RuntimeOperation) -> Result<(), String> {
        let mut state = self.state.lock().unwrap();
        if state.is_active() {
            return Err(format!(
                "session {} already has an active run ({state:?})",
                self.session_id
            ));
        }
        self.cancel.store(false, Ordering::SeqCst);
        self.provider_cancel.store(false, Ordering::SeqCst);
        self.steering.lock().unwrap().clear();
        self.follow_ups.lock().unwrap().clear();
        *state = RuntimeState::Running;
        if self
            .command_tx
            .send(RuntimeCommand::Execute(operation))
            .is_err()
        {
            *state = RuntimeState::Failed;
            return Err(format!(
                "runtime worker for session {} is unavailable",
                self.session_id
            ));
        }
        Ok(())
    }

    /// Inject guidance into the live run. The current provider stream is
    /// cooperatively interrupted, while the run itself remains active.
    pub fn steer(&self, text: &str, source: Option<Value>) -> Result<(), String> {
        let state = self.state.lock().unwrap();
        if !matches!(
            *state,
            RuntimeState::Running | RuntimeState::WaitingApproval | RuntimeState::WaitingUser
        ) {
            return Err(format!(
                "session {} cannot be steered while {state:?}",
                self.session_id
            ));
        }
        self.steering
            .lock()
            .unwrap()
            .push((text.to_string(), source));
        self.provider_cancel.store(true, Ordering::SeqCst);
        Ok(())
    }

    /// Queue a distinct run for the same session. The worker scheduler starts
    /// it only after the current run reaches a terminal boundary.
    pub fn follow_up(&self, text: &str, source: Option<Value>) -> Result<String, String> {
        let state = self.state.lock().unwrap();
        if !matches!(
            *state,
            RuntimeState::Running | RuntimeState::WaitingApproval | RuntimeState::WaitingUser
        ) {
            return Err(format!(
                "session {} has no active run to follow",
                self.session_id
            ));
        }
        let run_id = uuid_v4();
        self.follow_ups.lock().unwrap().push_back(QueuedRun {
            input: text.to_string(),
            source,
            run_id: run_id.clone(),
        });
        Ok(run_id)
    }

    /// Cancel the active run immediately. Pending follow-ups are discarded so
    /// cancellation cannot unexpectedly start more work.
    pub fn cancel(&self) -> bool {
        let mut state = self.state.lock().unwrap();
        if !state.is_active() {
            return false;
        }
        *state = RuntimeState::Cancelling;
        self.follow_ups.lock().unwrap().clear();
        self.cancel.store(true, Ordering::SeqCst);
        self.provider_cancel.store(true, Ordering::SeqCst);
        true
    }

    pub fn switch_model(&self, model: &str) -> Result<Option<String>, String> {
        let state = self.state.lock().unwrap();
        if state.is_active() {
            return Err(format!(
                "cannot switch model: session {} has an active run ({state:?})",
                self.session_id
            ));
        }
        let (reply, response) = mpsc::channel();
        self.command_tx
            .send(RuntimeCommand::SwitchModel {
                change: ModelChange::LegacyId(model.to_string()),
                reply,
            })
            .map_err(|_| "runtime worker is unavailable".to_string())?;
        response
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "runtime worker did not acknowledge model switch".to_string())?
    }

    pub fn switch_runtime_config(&self, config: RuntimeConfig) -> Result<Option<String>, String> {
        let state = self.state.lock().unwrap();
        if state.is_active() {
            return Err(format!(
                "cannot switch model: session {} has an active run ({state:?})",
                self.session_id
            ));
        }
        let (reply, response) = mpsc::channel();
        self.command_tx
            .send(RuntimeCommand::SwitchModel {
                change: ModelChange::Resolved(Box::new(config)),
                reply,
            })
            .map_err(|_| "runtime worker is unavailable".to_string())?;
        response
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "runtime worker did not acknowledge model switch".to_string())?
    }

    pub fn truncate_messages(&self, index: usize) -> Result<usize, String> {
        let state = self.state.lock().unwrap();
        if state.is_active() {
            return Err(format!(
                "cannot truncate messages: session {} has an active run ({state:?})",
                self.session_id
            ));
        }
        let (reply, response) = mpsc::channel();
        self.command_tx
            .send(RuntimeCommand::Truncate { index, reply })
            .map_err(|_| "runtime worker is unavailable".to_string())?;
        response
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "runtime worker did not acknowledge message truncation".to_string())?
    }
}

fn runtime_worker(
    mut host: RuntimeHost,
    command_rx: mpsc::Receiver<RuntimeCommand>,
    state: Arc<Mutex<RuntimeState>>,
    follow_ups: Arc<Mutex<VecDeque<QueuedRun>>>,
    messages: Arc<RwLock<Vec<Value>>>,
    cancel: Arc<AtomicBool>,
    provider_cancel: Arc<AtomicBool>,
) {
    while let Ok(command) = command_rx.recv() {
        match command {
            RuntimeCommand::Execute(operation) => execute_operation_chain(
                &mut host,
                operation,
                &state,
                &follow_ups,
                &messages,
                &cancel,
                &provider_cancel,
            ),
            RuntimeCommand::SwitchModel { change, reply } => {
                let notice = match change {
                    ModelChange::LegacyId(model) => host.switch_model(&model),
                    ModelChange::Resolved(config) => host.switch_runtime_config(*config),
                };
                sync_message_snapshot(&host, &messages);
                let _ = reply.send(Ok(notice));
            }
            RuntimeCommand::Truncate { index, reply } => {
                host.truncate_messages(index);
                sync_message_snapshot(&host, &messages);
                let _ = reply.send(Ok(host.messages().len()));
            }
        }
    }
}

fn execute_operation_chain(
    host: &mut RuntimeHost,
    mut operation: RuntimeOperation,
    state: &Arc<Mutex<RuntimeState>>,
    follow_ups: &Arc<Mutex<VecDeque<QueuedRun>>>,
    messages: &Arc<RwLock<Vec<Value>>>,
    cancel: &Arc<AtomicBool>,
    provider_cancel: &Arc<AtomicBool>,
) {
    loop {
        let result = match operation {
            RuntimeOperation::Run(run) => {
                host.set_run_id(run.run_id);
                host.run(&run.input, run.source)
            }
            RuntimeOperation::Resume { run_id } => {
                host.set_run_id(run_id);
                host.resume()
            }
            RuntimeOperation::Retry { run_id } => {
                host.set_run_id(run_id);
                host.retry()
            }
        };
        sync_message_snapshot(host, messages);
        let terminal_state = state_from_result(&result);

        // Holding state before the queue gives follow_up() an atomic choice:
        // either it queues while the run is active, or it observes terminal
        // state and is rejected. No accepted follow-up can be lost here.
        let mut current_state = state.lock().unwrap();
        let mut queued = follow_ups.lock().unwrap();
        if cancel.load(Ordering::SeqCst) {
            queued.clear();
            *current_state = RuntimeState::Interrupted;
            break;
        }
        if let Some(next) = queued.pop_front() {
            cancel.store(false, Ordering::SeqCst);
            provider_cancel.store(false, Ordering::SeqCst);
            *current_state = RuntimeState::Running;
            operation = RuntimeOperation::Run(next);
            drop(queued);
            drop(current_state);
            continue;
        }
        *current_state = terminal_state;
        break;
    }
}

fn sync_message_snapshot(host: &RuntimeHost, messages: &Arc<RwLock<Vec<Value>>>) {
    *messages.write().unwrap() = host.messages().to_vec();
}

fn state_from_result(result: &Result<Value, String>) -> RuntimeState {
    match result {
        Err(_) => RuntimeState::Failed,
        Ok(value) if value.get("status").and_then(Value::as_str) == Some("interrupted") => {
            RuntimeState::Interrupted
        }
        Ok(value)
            if value.get("status").and_then(Value::as_str) == Some("max_iterations_exceeded") =>
        {
            RuntimeState::Failed
        }
        Ok(_) => RuntimeState::Completed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Read};
    use std::net::{TcpListener, TcpStream};
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn test_runtime_config_default() {
        let cfg = RuntimeConfig::default();
        assert_eq!(cfg.protocol, "openai_chat");
        assert_eq!(cfg.max_iterations, DEFAULT_MAX_ITERATIONS);
        assert_eq!(cfg.max_retries, 2);
    }

    #[test]
    fn test_event_to_frame() {
        let event = RuntimeEvent::TurnStart {
            input: Value::String("hello".to_string()),
            source: None,
            run_id: Some("run-1".to_string()),
        };
        let frame = event.to_frame("session-1", 1);
        assert_eq!(frame["type"], "turn_start");
        assert_eq!(frame["version"], 1);
        assert_eq!(frame["sessionId"], "session-1");
        assert_eq!(frame["sequence"], 1);
        assert_eq!(frame["payload"]["run_id"], "run-1");
    }

    #[test]
    fn test_cancel_flag() {
        let host = RuntimeHost::new("s1", RuntimeConfig::default());
        assert!(!host.cancel.load(Ordering::Relaxed));
        host.cancel();
        assert!(host.cancel.load(Ordering::Relaxed));
        host.reset_cancel();
        assert!(!host.cancel.load(Ordering::Relaxed));
    }

    #[test]
    fn test_steering_queue() {
        let mut host = RuntimeHost::new("s1", RuntimeConfig::default());
        host.steer("change direction", None);
        assert!(host.drain_steering());
        assert!(!host.drain_steering());
    }

    #[test]
    fn test_follow_up_queue_belongs_to_active_handle() {
        let handle = RuntimeHandle::spawn(RuntimeHost::new("s1", RuntimeConfig::default()))
            .expect("spawn runtime");
        *handle.state.lock().unwrap() = RuntimeState::Running;
        let run_id = handle
            .follow_up("then do this", None)
            .expect("queue follow-up");
        let queued = handle.follow_ups.lock().unwrap();
        assert_eq!(queued.len(), 1);
        assert_eq!(queued[0].input, "then do this");
        assert_eq!(queued[0].run_id, run_id);
    }

    #[test]
    fn test_switch_model() {
        let mut host = RuntimeHost::new(
            "s1",
            RuntimeConfig {
                model: "gpt-5.5".to_string(),
                ..Default::default()
            },
        );
        host.messages.push(json!({"role": "user", "content": "hi"}));
        let notice = host.switch_model("claude-sonnet-4-6");
        assert!(notice.is_some());
        assert_eq!(host.model(), "claude-sonnet-4-6");
        assert!(host.switch_model("claude-sonnet-4-6").is_none());
    }

    #[test]
    fn test_truncate_messages() {
        let mut host = RuntimeHost::new("s1", RuntimeConfig::default());
        host.messages.push(json!({"role": "user", "content": "a"}));
        host.messages
            .push(json!({"role": "assistant", "content": "b"}));
        host.messages.push(json!({"role": "user", "content": "c"}));
        host.truncate_messages(1);
        assert_eq!(host.messages().len(), 1);
    }

    #[test]
    fn test_outbound_strips_sidecars() {
        let host = RuntimeHost::new("s1", RuntimeConfig::default());
        let msgs = vec![
            json!({"role": "user", "content": "hello", "ts": 123.0, "source": {"connector": "slack"}}),
            json!({"role": "assistant", "content": "hi"}),
            json!({"role": "notice", "kind": "error", "text": "bad"}),
        ];
        let host = host.with_messages(msgs);
        let outbound = host.outbound_messages();
        assert_eq!(outbound.len(), 2);
        assert!(outbound[0].get("ts").is_none());
        assert!(outbound[0].get("source").is_none());
        assert!(outbound[1].get("ts").is_none());
    }

    #[test]
    fn test_is_retryable() {
        assert!(is_retryable_error("HTTP 429: rate limited"));
        assert!(is_retryable_error("HTTP 503: service unavailable"));
        assert!(is_retryable_error("connection refused"));
        assert!(is_retryable_error("timeout waiting for response"));
        assert!(!is_retryable_error("HTTP 400: bad request"));
        assert!(!is_retryable_error("invalid api key"));
    }

    #[test]
    fn tool_schema_validation_rejects_missing_required_arguments() {
        let schema = json!({
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}}
        });
        assert!(validate_schema_value(&json!({}), &schema, "arguments").is_err());
        assert!(validate_schema_value(&json!({"path": "out.txt"}), &schema, "arguments").is_ok());
    }

    struct CountingExecutor {
        calls: Arc<AtomicUsize>,
        staged: Option<StagedArtifact>,
    }

    impl ToolExecutor for CountingExecutor {
        fn execute(&self, call: &ToolCall) -> ToolResult {
            self.calls.fetch_add(1, Ordering::SeqCst);
            let mut result = ToolResult::success(&call.id, json!({"ok": true}));
            if let Some(staged) = &self.staged {
                result.staged_artifacts.push(staged.clone());
            }
            result
        }
    }

    fn tool_contract(risk: &str, requires_approval: bool) -> Value {
        json!([{
            "type": "function",
            "function": {
                "name": "write_report",
                "parameters": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}}
                },
                "metadata": {
                    "risk_level": risk,
                    "requires_approval": requires_approval,
                    "category": if risk == "low" { "read" } else { "filesystem" },
                    "capabilities": []
                }
            }
        }])
    }

    #[test]
    fn tool_execution_composes_policy_lifecycle_validation_checkpoint_and_ledger() {
        let temp = tempfile::tempdir().unwrap();
        let authorities = RuntimeAuthorities::open(temp.path()).unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let mut host = RuntimeHost::new("session-1", RuntimeConfig::default())
            .with_authorities(authorities.clone())
            .with_tools(tool_contract("low", false))
            .with_tool_executor(Arc::new(CountingExecutor {
                calls: calls.clone(),
                staged: None,
            }));
        host.set_run_id("run-1".to_string());
        let result = host.execute_tool_call(&ToolCall {
            id: "call-1".to_string(),
            name: "write_report".to_string(),
            arguments: json!({"path": "report.md"}),
        });
        assert!(result.error.is_none(), "{:?}", result.error);
        assert_eq!(calls.load(Ordering::SeqCst), 1);

        let events = authorities
            .ledger
            .lock()
            .unwrap()
            .reader()
            .unwrap()
            .events("run-1")
            .unwrap();
        let event_types: Vec<&str> = events.iter().map(|event| event.r#type.as_str()).collect();
        for expected in [
            "tool.proposed",
            "tool.approved",
            "tool.started",
            "validation.registered",
            "tool.completed",
            "checkpoint.registered",
        ] {
            assert!(
                event_types.contains(&expected),
                "missing {expected}: {event_types:?}"
            );
        }
        let entry = authorities
            .idempotency
            .lock()
            .unwrap()
            .get("run-1", "call-1")
            .unwrap()
            .unwrap();
        assert_eq!(entry.state, crate::idemlog::SideEffectState::Committed);
        let approvals = authorities
            .approvals
            .lock()
            .unwrap()
            .list(10, Some("session-1"), None, Some("write_report"))
            .unwrap();
        assert_eq!(approvals[0]["status"], "auto_approved");
    }

    #[test]
    fn approval_authority_blocks_execution_until_ipc_resolution() {
        let temp = tempfile::tempdir().unwrap();
        let authorities = RuntimeAuthorities::open(temp.path()).unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let mut host = RuntimeHost::new("session-1", RuntimeConfig::default())
            .with_authorities(authorities)
            .with_tools(tool_contract("medium", true))
            .with_tool_executor(Arc::new(CountingExecutor {
                calls: calls.clone(),
                staged: None,
            }));
        host.set_run_id("run-approval".to_string());
        let host = Arc::new(host);
        let worker_host = host.clone();
        let worker = std::thread::spawn(move || {
            worker_host.execute_tool_call(&ToolCall {
                id: "call-approval".to_string(),
                name: "write_report".to_string(),
                arguments: json!({"path": "report.md"}),
            })
        });
        for _ in 0..100 {
            if host.approvals.pending_ids() == ["call-approval"] {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(calls.load(Ordering::SeqCst), 0);
        host.approvals
            .resolve(Some("call-approval"), ApprovalDecision::Once)
            .unwrap();
        let result = worker.join().unwrap();
        assert!(result.error.is_none(), "{:?}", result.error);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn runtime_promotes_and_hashes_only_staged_worker_artifacts() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("workspace");
        let staging = workspace.join(".delta/staging/run-artifact");
        fs::create_dir_all(&staging).unwrap();
        let staged_path = staging.join("candidate.md");
        fs::write(&staged_path, "authoritative artifact").unwrap();
        let authorities = RuntimeAuthorities::open(temp.path().join("state")).unwrap();
        let mut host = RuntimeHost::new(
            "session-1",
            RuntimeConfig {
                workspace: Some(workspace.to_string_lossy().to_string()),
                ..RuntimeConfig::default()
            },
        )
        .with_authorities(authorities.clone())
        .with_tools(tool_contract("low", false))
        .with_tool_executor(Arc::new(CountingExecutor {
            calls: Arc::new(AtomicUsize::new(0)),
            staged: Some(StagedArtifact {
                staging_path: staged_path.clone(),
                relative_path: "reports/final.md".to_string(),
                kind: "markdown".to_string(),
                incomplete: false,
            }),
        }));
        host.set_run_id("run-artifact".to_string());
        let result = host.execute_tool_call(&ToolCall {
            id: "call-artifact".to_string(),
            name: "write_report".to_string(),
            arguments: json!({"path": "reports/final.md"}),
        });
        assert!(result.error.is_none(), "{:?}", result.error);
        assert!(!staged_path.exists());
        assert_eq!(
            fs::read_to_string(workspace.join("reports/final.md")).unwrap(),
            "authoritative artifact"
        );
        let artifacts = authorities
            .ledger
            .lock()
            .unwrap()
            .reader()
            .unwrap()
            .events("run-artifact")
            .unwrap();
        assert!(artifacts
            .iter()
            .any(|event| event.r#type == "artifact.registered"));
        assert!(result.output["artifacts"][0]["sha256"]
            .as_str()
            .is_some_and(|hash| hash.len() == 64));
    }

    #[derive(Default)]
    struct CaptureSink {
        frames: Mutex<Vec<Value>>,
    }

    struct MockProvider {
        base_url: String,
        first_response_started: mpsc::Receiver<()>,
        release_first_response: mpsc::Sender<()>,
        request_bodies: Arc<Mutex<Vec<String>>>,
        worker: std::thread::JoinHandle<()>,
    }

    impl MockProvider {
        fn start(response_count: usize) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").expect("bind mock provider");
            let address = listener.local_addr().expect("mock provider address");
            let (started_tx, first_response_started) = mpsc::channel();
            let (release_first_response, release_rx) = mpsc::channel();
            let request_bodies = Arc::new(Mutex::new(Vec::new()));
            let captured = request_bodies.clone();
            let worker = std::thread::spawn(move || {
                for response_index in 0..response_count {
                    let (mut stream, _) = listener.accept().expect("accept provider request");
                    stream
                        .set_read_timeout(Some(Duration::from_secs(5)))
                        .unwrap();
                    let body = read_http_request(&mut stream);
                    captured.lock().unwrap().push(body);
                    write!(
                        stream,
                        "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                    )
                    .unwrap();
                    if response_index == 0 {
                        write_openai_delta(&mut stream, "first");
                        stream.flush().unwrap();
                        started_tx.send(()).unwrap();
                        release_rx
                            .recv_timeout(Duration::from_secs(5))
                            .expect("release first provider response");
                    } else {
                        write_openai_delta(&mut stream, "second");
                    }
                    write!(
                        stream,
                        "data: {{\"choices\":[{{\"delta\":{{}},\"finish_reason\":\"stop\"}}]}}\n\ndata: [DONE]\n\n"
                    )
                    .unwrap();
                    stream.flush().unwrap();
                }
            });
            Self {
                base_url: format!("http://{address}/v1"),
                first_response_started,
                release_first_response,
                request_bodies,
                worker,
            }
        }

        fn finish(self) -> Vec<String> {
            self.worker.join().expect("mock provider worker");
            self.request_bodies.lock().unwrap().clone()
        }
    }

    fn read_http_request(stream: &mut TcpStream) -> String {
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut content_length = 0usize;
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).expect("read request header");
            if line == "\r\n" || line.is_empty() {
                break;
            }
            if let Some(length) = line
                .to_ascii_lowercase()
                .strip_prefix("content-length:")
                .and_then(|value| value.trim().parse::<usize>().ok())
            {
                content_length = length;
            }
        }
        let mut body = vec![0u8; content_length];
        reader.read_exact(&mut body).expect("read request body");
        String::from_utf8(body).expect("request body is utf-8")
    }

    fn write_openai_delta(stream: &mut TcpStream, text: &str) {
        writeln!(
            stream,
            "data: {}\n",
            json!({"choices": [{"delta": {"content": text}, "finish_reason": null}]})
        )
        .unwrap();
    }

    fn provider_test_host(base_url: String, sink: Arc<CaptureSink>) -> RuntimeHost {
        RuntimeHost::new(
            "session-1",
            RuntimeConfig {
                model: "test-model".to_string(),
                protocol: "openai_chat".to_string(),
                api_key: "test-key".to_string(),
                base_url,
                max_iterations: 2,
                ..RuntimeConfig::default()
            },
        )
        .with_event_sink(sink)
    }

    fn wait_for_terminal(handle: &RuntimeHandle) {
        for _ in 0..500 {
            if !handle.state().is_active() {
                return;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        panic!(
            "runtime did not reach a terminal state: {:?}",
            handle.state()
        );
    }

    impl EventSink for CaptureSink {
        fn emit(&self, frame: Value) {
            self.frames.lock().unwrap().push(frame);
        }
    }

    #[test]
    fn provider_frames_are_converted_to_runtime_envelopes() {
        let sink = Arc::new(CaptureSink::default());
        let emitter = RuntimeEventEmitter {
            session_id: "session-1".to_string(),
            sequence: Arc::new(Mutex::new(0)),
            sink: sink.clone(),
        };
        let mut writer = ProviderEventWriter::new(emitter);
        writer
            .write_all(
                b"{\"ok\":true,\"stream\":\"delta\",\"request_id\":\"private\",\"data\":{\"text_delta\":\"hi\"}}\n",
            )
            .unwrap();
        let (text, reasoning) = writer.finish();
        assert_eq!(text, "hi");
        assert!(reasoning.is_empty());

        let frames = sink.frames.lock().unwrap();
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0]["type"], "assistant_delta");
        assert_eq!(frames[0]["version"], 1);
        assert_eq!(frames[0]["sessionId"], "session-1");
        assert_eq!(frames[0]["sequence"], 1);
        assert_eq!(frames[0]["payload"]["text"], "hi");
        assert!(frames[0].get("stream").is_none());
        assert!(frames[0].get("request_id").is_none());
    }

    #[test]
    fn runtime_handle_runs_in_background_and_rejects_parallel_run() {
        let sink = Arc::new(CaptureSink::default());
        let host = RuntimeHost::new(
            "session-1",
            RuntimeConfig {
                max_iterations: 0,
                ..RuntimeConfig::default()
            },
        )
        .with_event_sink(sink.clone());
        let handle = RuntimeHandle::spawn(host).expect("spawn runtime");
        let run_id = handle.run("hello".to_string(), None).expect("start run");
        assert!(handle.run("parallel".to_string(), None).is_err());

        for _ in 0..100 {
            if !handle.state().is_active() {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(handle.state(), RuntimeState::Failed);
        let frames = sink.frames.lock().unwrap();
        assert_eq!(frames[0]["type"], "turn_start");
        assert_eq!(frames[0]["payload"]["run_id"], run_id);
        assert_eq!(frames[1]["type"], "turn_end");
        assert_eq!(frames[1]["sequence"], 2);
    }

    #[test]
    fn steering_interrupts_provider_and_changes_the_same_run() {
        let provider = MockProvider::start(2);
        let sink = Arc::new(CaptureSink::default());
        let handle =
            RuntimeHandle::spawn(provider_test_host(provider.base_url.clone(), sink.clone()))
                .expect("spawn runtime");
        let run_id = handle.run("initial".to_string(), None).expect("start run");
        provider
            .first_response_started
            .recv_timeout(Duration::from_secs(5))
            .expect("first provider delta");
        handle.steer("change direction", None).expect("steer run");
        provider.release_first_response.send(()).unwrap();
        wait_for_terminal(&handle);
        assert_eq!(handle.state(), RuntimeState::Completed);

        let request_bodies = provider.finish();
        assert_eq!(request_bodies.len(), 2);
        assert!(request_bodies[1].contains("change direction"));
        let frames = sink.frames.lock().unwrap();
        let turn_starts: Vec<&Value> = frames
            .iter()
            .filter(|frame| frame["type"] == "turn_start")
            .collect();
        assert_eq!(turn_starts.len(), 1);
        assert_eq!(turn_starts[0]["payload"]["run_id"], run_id);
        assert!(frames.iter().all(|frame| frame.get("stream").is_none()));
        assert!(frames
            .windows(2)
            .all(|pair| pair[1]["sequence"].as_u64() > pair[0]["sequence"].as_u64()));
    }

    #[test]
    fn follow_up_is_scheduled_as_a_distinct_run() {
        let provider = MockProvider::start(2);
        let sink = Arc::new(CaptureSink::default());
        let handle =
            RuntimeHandle::spawn(provider_test_host(provider.base_url.clone(), sink.clone()))
                .expect("spawn runtime");
        let first_run_id = handle.run("first task".to_string(), None).unwrap();
        provider
            .first_response_started
            .recv_timeout(Duration::from_secs(5))
            .expect("first provider delta");
        let follow_up_run_id = handle.follow_up("second task", None).unwrap();
        assert_ne!(first_run_id, follow_up_run_id);
        provider.release_first_response.send(()).unwrap();
        wait_for_terminal(&handle);
        assert_eq!(handle.state(), RuntimeState::Completed);
        assert_eq!(provider.finish().len(), 2);

        let frames = sink.frames.lock().unwrap();
        let run_ids: Vec<&str> = frames
            .iter()
            .filter(|frame| frame["type"] == "turn_start")
            .filter_map(|frame| frame["payload"]["run_id"].as_str())
            .collect();
        assert_eq!(
            run_ids,
            vec![first_run_id.as_str(), follow_up_run_id.as_str()]
        );
    }

    #[test]
    fn cancel_transitions_active_run_to_interrupted() {
        let provider = MockProvider::start(1);
        let sink = Arc::new(CaptureSink::default());
        let handle =
            RuntimeHandle::spawn(provider_test_host(provider.base_url.clone(), sink.clone()))
                .expect("spawn runtime");
        handle.run("cancel me".to_string(), None).unwrap();
        provider
            .first_response_started
            .recv_timeout(Duration::from_secs(5))
            .expect("first provider delta");
        assert!(handle.cancel());
        assert_eq!(handle.state(), RuntimeState::Cancelling);
        provider.release_first_response.send(()).unwrap();
        wait_for_terminal(&handle);
        assert_eq!(handle.state(), RuntimeState::Interrupted);
        assert_eq!(provider.finish().len(), 1);
        assert!(sink
            .frames
            .lock()
            .unwrap()
            .iter()
            .any(|frame| frame["type"] == "interrupted"));
    }
}
