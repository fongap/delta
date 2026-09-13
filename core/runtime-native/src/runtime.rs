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
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex, RwLock};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};

use crate::provider::{self, ProviderRequest};
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

#[derive(Debug, Clone)]
pub struct ToolResult {
    pub tool_call_id: String,
    pub output: Value,
    pub error: Option<String>,
}

pub trait Approver: Send + Sync {
    fn approve(&self, tool_call_id: &str, name: &str, arguments: &Value, reason: &str) -> bool;
}

pub struct DenyAll;
impl Approver for DenyAll {
    fn approve(&self, _: &str, _: &str, _: &Value, _: &str) -> bool {
        false
    }
}

#[derive(Clone)]
pub struct RuntimeConfig {
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
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
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
    ledger: Option<Arc<Mutex<LedgerWriter>>>,
    tools: Option<Value>,
    tool_executor: Option<Arc<dyn ToolExecutor>>,
    approver: Arc<dyn Approver>,
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
        model: String,
        reply: mpsc::Sender<Result<Option<String>, String>>,
    },
    Truncate {
        index: usize,
        reply: mpsc::Sender<Result<usize, String>>,
    },
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
            ledger: None,
            tools: None,
            tool_executor: None,
            approver: Arc::new(DenyAll),
            run_id: None,
            sink: Arc::new(NullSink),
        }
    }

    pub fn with_tools(mut self, tools: Value) -> Self {
        self.tools = Some(tools);
        self
    }
    pub fn with_tool_executor(mut self, executor: Arc<dyn ToolExecutor>) -> Self {
        self.tool_executor = Some(executor);
        self
    }
    pub fn with_approver(mut self, approver: Arc<dyn Approver>) -> Self {
        self.approver = approver;
        self
    }
    pub fn with_ledger(mut self, writer: Arc<Mutex<LedgerWriter>>) -> Self {
        self.ledger = Some(writer);
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
        &self.config.model
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
        if model.is_empty() || model == self.config.model {
            return None;
        }
        let had_history = self
            .messages
            .iter()
            .any(|m| m.get("role").and_then(|r| r.as_str()) != Some("system"));
        self.config.model = model.to_string();
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

    fn ledger_transition(&self, event_type: &str, actor: &str, payload: Value) {
        if let Some(ref ledger) = self.ledger {
            let run_id = self.run_id.clone().unwrap_or_else(uuid_v4);
            let ws = self.config.workspace.clone().unwrap_or_default();
            if let Ok(w) = ledger.lock() {
                let _ = w.transition(&run_id, event_type, actor, now_ts(), &payload, &ws);
            }
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
                self.emit_event(RuntimeEvent::ToolProposed {
                    tool_call_id: tc.id.clone(),
                    name: tc.name.clone(),
                    arguments: tc.arguments.clone(),
                    risk_level: None,
                });
            }
            for tc in &turn.tool_calls {
                if self.cancel.load(Ordering::Relaxed) {
                    break;
                }
                self.emit_event(RuntimeEvent::ToolStarted {
                    tool_call_id: tc.id.clone(),
                    name: tc.name.clone(),
                });
                let result = if let Some(ref executor) = self.tool_executor {
                    executor.execute(tc)
                } else {
                    ToolResult {
                        tool_call_id: tc.id.clone(),
                        output: Value::String("Tool execution not available".to_string()),
                        error: Some("no tool executor configured".to_string()),
                    }
                };
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
        self.ledger_transition("run.started", "user", json!({"kind": "run"}));
        let result = self.loop_turn();
        match &result {
            Ok(s) if s == "completed" => {
                self.ledger_transition("run.completed", "system", json!({"kind": "run"}));
            }
            Ok(s) if s == "interrupted" => {
                self.ledger_transition("run.interrupted", "system", json!({"kind": "run"}));
            }
            Ok(_) => {
                self.ledger_transition("run.completed", "system", json!({"kind": "run"}));
            }
            Err(e) => {
                self.ledger_transition("run.failed", "system", json!({"reason": e, "kind": "run"}));
            }
        }
        result.map(|s| json!({"status": s}))
    }

    pub fn resume(&mut self) -> Result<Value, String> {
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String("(resumed)".to_string()),
            source: None,
            run_id: self.run_id.clone(),
        });
        self.ledger_transition("run.resumed", "system", json!({"kind": "resume"}));
        self.loop_turn().map(|s| json!({"status": s}))
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
        self.loop_turn().map(|s| json!({"status": s}))
    }
}

impl RuntimeHandle {
    /// Start a dedicated worker thread for `host`. The returned handle is idle;
    /// callers must register it before calling [`run`](Self::run).
    pub fn spawn(host: RuntimeHost) -> Result<Self, String> {
        let session_id = host.session_id.clone();
        let cancel = host.cancel.clone();
        let provider_cancel = host.provider_cancel.clone();
        let steering = host.steering.clone();
        let state = Arc::new(Mutex::new(RuntimeState::Idle));
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
                model: model.to_string(),
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
            RuntimeCommand::SwitchModel { model, reply } => {
                let notice = host.switch_model(&model);
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
    fn test_deny_all_approver() {
        let approver = DenyAll;
        assert!(!approver.approve("tc1", "shell", &json!({}), "test"));
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
