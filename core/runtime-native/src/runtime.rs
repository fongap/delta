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

use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

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
    TurnStart { input: Value, source: Option<Value> },
    AssistantDelta { text: String },
    ReasoningDelta { text: String },
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
    ToolStarted { tool_call_id: String, name: String },
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
    IterationEnd { iteration: usize },
    TurnEnd { status: String, iterations: usize },
    Error { error: String, error_type: String },
    Interrupted { iterations: usize },
    Compacting,
    Compacted { text: String },
    ModelChanged { model: String },
}

impl RuntimeEvent {
    pub fn to_frame(&self, session_id: &str, sequence: u64) -> Value {
        let (event_type, payload) = match self {
            Self::TurnStart { input, source } => {
                ("turn_start", json!({"input": input, "source": source}))
            }
            Self::AssistantDelta { text } => {
                ("assistant_delta", json!({"text": text}))
            }
            Self::ReasoningDelta { text } => {
                ("reasoning_delta", json!({"text": text}))
            }
            Self::AssistantMessage { text, tool_calls, reasoning, usage } => (
                "assistant_message",
                json!({"text": text, "tool_calls": tool_calls, "reasoning": reasoning, "usage": usage}),
            ),
            Self::ToolProposed { tool_call_id, name, arguments, risk_level } => (
                "tool_proposed",
                json!({"tool_call_id": tool_call_id, "name": name, "arguments": arguments, "risk_level": risk_level}),
            ),
            Self::ToolStarted { tool_call_id, name } => (
                "tool_started",
                json!({"tool_call_id": tool_call_id, "name": name}),
            ),
            Self::ToolFinished { tool_call_id, name, result, error } => (
                "tool_finished",
                json!({"tool_call_id": tool_call_id, "name": name, "result": result, "error": error}),
            ),
            Self::PermissionRequired { tool_call_id, name, arguments, reason } => (
                "permission_required",
                json!({"tool_call_id": tool_call_id, "name": name, "arguments": arguments, "reason": reason}),
            ),
            Self::IterationEnd { iteration } => {
                ("iteration_end", json!({"iteration": iteration}))
            }
            Self::TurnEnd { status, iterations } => {
                ("turn_end", json!({"status": status, "iterations": iterations}))
            }
            Self::Error { error, error_type } => {
                ("error", json!({"error": error, "error_type": error_type}))
            }
            Self::Interrupted { iterations } => {
                ("interrupted", json!({"iterations": iterations}))
            }
            Self::Compacting => ("compacting", json!({})),
            Self::Compacted { text } => ("compacted", json!({"text": text})),
            Self::ModelChanged { model } => {
                ("model_changed", json!({"model": model}))
            }
        };
        json!({
            "type": event_type,
            "version": 1,
            "sessionId": session_id,
            "sequence": sequence,
            "payload": payload,
        })
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
    steering: Arc<Mutex<Vec<(String, Option<Value>)>>>,
    follow_ups: Arc<Mutex<Vec<(String, Option<Value>)>>>,
    sequence: Arc<Mutex<u64>>,
    session_id: String,
    ledger: Option<Arc<Mutex<LedgerWriter>>>,
    tools: Option<Value>,
    tool_executor: Option<Arc<dyn ToolExecutor>>,
    approver: Arc<dyn Approver>,
    run_id: Option<String>,
    sink: Arc<dyn EventSink>,
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

/// Adapter: the provider stream writes JSON delta frames to a `Write`;
/// this wraps an `EventSink` so deltas go through the same channel
/// as runtime events (stdout for delta_core, Tauri emit for desktop).
struct SinkWriter {
    sink: Arc<dyn EventSink>,
}

impl Write for SinkWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let text = String::from_utf8_lossy(buf);
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            if let Ok(frame) = serde_json::from_str::<Value>(trimmed) {
                self.sink.emit(frame);
            }
        }
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
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
            steering: Arc::new(Mutex::new(Vec::new())),
            follow_ups: Arc::new(Mutex::new(Vec::new())),
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

    /// Install a custom event sink (e.g. Tauri emit). Default is NullSink.
    pub fn with_event_sink(mut self, sink: Arc<dyn EventSink>) -> Self {
        self.sink = sink;
        self
    }

    pub fn reset_cancel(&self) {
        self.cancel.store(false, Ordering::SeqCst);
    }
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
    }
    pub fn steer(&self, text: &str, source: Option<Value>) {
        self.steering.lock().unwrap().push((text.to_string(), source));
    }
    pub fn follow_up(&self, text: &str, source: Option<Value>) {
        self.follow_ups.lock().unwrap().push((text.to_string(), source));
    }
    pub fn drain_follow_ups(&self) -> Vec<(String, Option<Value>)> {
        std::mem::take(&mut *self.follow_ups.lock().unwrap())
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
        let mut seq = self.sequence.lock().unwrap();
        *seq += 1;
        let frame = event.to_frame(&self.session_id, *seq);
        self.sink.emit(frame);
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
        cancel: &Arc<AtomicBool>,
        turn: &mut Option<AssistantTurn>,
        streamed_text: &mut Vec<String>,
        streamed_reasoning: &mut Vec<String>,
    ) -> Result<(), String> {
        let sink = self.sink.clone();
        let mut writer = SinkWriter { sink };
        let result = provider::stream(req, &mut writer, stream_id, cancel)?;
        if result.get("cancelled").and_then(|v| v.as_bool()).unwrap_or(false) {
            return Ok(());
        }
        let text = result.get("text").and_then(|t| t.as_str()).map(String::from);
        let reasoning = result.get("reasoning").and_then(|r| r.as_str()).map(String::from);
        let finish_reason = result.get("finish_reason").and_then(|f| f.as_str()).map(String::from);
        let usage = result.get("usage").cloned();
        let tool_calls: Vec<ToolCall> = result
            .get("tool_calls")
            .and_then(|tcs| tcs.as_array())
            .map(|tcs| {
                tcs.iter()
                    .map(|tc| ToolCall {
                        id: tc.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        name: tc.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        arguments: tc.get("arguments").cloned().unwrap_or(json!({})),
                    })
                    .collect()
            })
            .unwrap_or_default();
        if let Some(ref t) = text {
            streamed_text.push(t.clone());
        }
        if let Some(ref r) = reasoning {
            streamed_reasoning.push(r.clone());
        }
        *turn = Some(AssistantTurn { text, reasoning, tool_calls, finish_reason, usage });
        Ok(())
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
                self.messages.push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                self.emit_event(RuntimeEvent::Interrupted { iterations });
                return Ok("interrupted".to_string());
            }
            let req = self.build_request();
            let cancel = self.cancel.clone();
            let mut turn: Option<AssistantTurn> = None;
            let mut streamed_text: Vec<String> = Vec::new();
            let mut streamed_reasoning: Vec<String> = Vec::new();
            let stream_id = uuid_v4();
            match self.stream_provider(&req, &stream_id, &cancel, &mut turn, &mut streamed_text, &mut streamed_reasoning) {
                Ok(()) => {}
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
                    self.messages.push(json!({"role": "notice", "kind": "error", "text": &e, "ts": now_ts()}));
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
                self.messages.push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                self.emit_event(RuntimeEvent::Interrupted { iterations });
                return Ok("interrupted".to_string());
            }
            let turn = turn.unwrap_or_default();
            self.messages.push(self.assistant_message(
                turn.text.as_deref(),
                turn.reasoning.as_deref(),
                &turn.tool_calls,
            ));
            let tool_call_names: Vec<String> = turn.tool_calls.iter().map(|tc| tc.name.clone()).collect();
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
                self.emit_event(RuntimeEvent::TurnEnd { status: "completed".to_string(), iterations });
                let follow_ups = self.drain_follow_ups();
                if !follow_ups.is_empty() {
                    for (text, source) in follow_ups {
                        self.run(&text, source)?;
                    }
                }
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
            self.emit_event(RuntimeEvent::IterationEnd { iteration: iterations });
            if self.cancel.load(Ordering::Relaxed) {
                self.messages.push(json!({"role": "notice", "kind": "interrupted", "ts": now_ts()}));
                self.emit_event(RuntimeEvent::Interrupted { iterations });
                return Ok("interrupted".to_string());
            }
            self.drain_steering();
        }
    }

    pub fn run(&mut self, user_input: &str, source: Option<Value>) -> Result<Value, String> {
        self.reset_cancel();
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String(user_input.to_string()),
            source: source.clone(),
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
        self.reset_cancel();
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String("(resumed)".to_string()),
            source: None,
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
        self.reset_cancel();
        self.emit_event(RuntimeEvent::TurnStart {
            input: Value::String(String::new()),
            source: None,
        });
        self.loop_turn().map(|s| json!({"status": s}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
        };
        let frame = event.to_frame("session-1", 1);
        assert_eq!(frame["type"], "turn_start");
        assert_eq!(frame["version"], 1);
        assert_eq!(frame["sessionId"], "session-1");
        assert_eq!(frame["sequence"], 1);
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
    fn test_follow_up_queue() {
        let host = RuntimeHost::new("s1", RuntimeConfig::default());
        host.follow_up("then do this", None);
        let ups = host.drain_follow_ups();
        assert_eq!(ups.len(), 1);
        assert_eq!(ups[0].0, "then do this");
        assert!(host.drain_follow_ups().is_empty());
    }

    #[test]
    fn test_switch_model() {
        let mut host = RuntimeHost::new("s1", RuntimeConfig {
            model: "gpt-5.5".to_string(),
            ..Default::default()
        });
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
        host.messages.push(json!({"role": "assistant", "content": "b"}));
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
}
