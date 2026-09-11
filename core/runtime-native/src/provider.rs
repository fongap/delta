//! R5 / ADR-047 Phase 1: Provider wire-protocol transport.
//!
//! Implements OpenAI Chat Completions HTTP calls in Rust.
//! Response format matches the Python `AssistantTurn` / `StreamChunk`
//! contract (base.py) so the engine keeps working unchanged.

use std::io::{BufRead, BufReader, Write};

use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Debug, Deserialize)]
pub struct ProviderRequest {
    pub protocol: String,
    pub model: String,
    pub messages: Value,
    #[serde(default)]
    pub tools: Option<Value>,
    #[serde(default)]
    pub settings: Option<Value>,
    pub api_key: String,
    pub base_url: String,
}

#[derive(Default)]
struct ToolCallAccum {
    id: String,
    name: String,
    args: String,
}

fn agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout(std::time::Duration::from_secs(300))
        .build()
}

/// Convert a ureq error into a message string that includes the HTTP response body.
/// The Python providers match on error-body markers (e.g. "reasoning_effort",
/// "'max_tokens' is not supported") to drive param-fix retry, so the body must be
/// carried in the error string.
fn http_error(e: ureq::Error) -> String {
    match e {
        ureq::Error::Status(code, response) => {
            let body = response.into_string().unwrap_or_default();
            let truncated: &str = if body.len() > 800 {
                &body[..800]
            } else {
                &body
            };
            format!("HTTP {code}: {truncated}")
        }
        ureq::Error::Transport(t) => format!("transport error: {t}"),
    }
}

/// Send the request and map ureq errors (with body) to String.
fn send_json(req_builder: ureq::Request, body: Value) -> Result<ureq::Response, String> {
    req_builder.send_json(body).map_err(http_error)
}

fn build_openai_chat_body(req: &ProviderRequest, stream: bool) -> Value {
    let mut body = json!({
        "model": req.model,
        "messages": req.messages,
    });
    if stream {
        body["stream"] = json!(true);
        body["stream_options"] = json!({"include_usage": true});
    }
    if let Some(tools) = &req.tools {
        body["tools"] = tools.clone();
    }
    if let Some(settings) = &req.settings {
        if let Some(obj) = settings.as_object() {
            if let Some(body_obj) = body.as_object_mut() {
                for (k, v) in obj {
                    body_obj.insert(k.clone(), v.clone());
                }
            }
        }
    }
    body
}

fn usage_from_json(u: &Value) -> Value {
    let prompt = u.get("prompt_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
    let completion = u
        .get("completion_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let cached = u
        .get("prompt_tokens_details")
        .and_then(|d| d.get("cached_tokens"))
        .and_then(|c| c.as_i64())
        .unwrap_or(0);
    json!({
        "input": prompt - cached,
        "output": completion,
        "cache_read": cached,
        "cache_write": 0,
    })
}

pub fn complete_openai_chat(req: &ProviderRequest) -> Result<Value, String> {
    let url = format!("{}/chat/completions", req.base_url.trim_end_matches('/'));
    let body = build_openai_chat_body(req, false);
    let resp = send_json(
        agent()
            .post(&url)
            .set("Authorization", &format!("Bearer {}", req.api_key))
            .set("Content-Type", "application/json"),
        body,
    )?;
    let resp_json: Value = resp.into_json().map_err(|e| format!("JSON parse: {e}"))?;
    parse_openai_chat_response(&resp_json)
}

fn parse_openai_chat_response(resp: &Value) -> Result<Value, String> {
    let choices = resp
        .get("choices")
        .and_then(|c| c.as_array())
        .ok_or("missing choices")?;
    let choice = choices.first().ok_or("empty choices")?;
    let message = choice.get("message").ok_or("missing message")?;
    let text = message
        .get("content")
        .and_then(|c| c.as_str())
        .map(|s| s.to_string());
    let finish_reason = choice
        .get("finish_reason")
        .and_then(|f| f.as_str())
        .map(|s| s.to_string());
    let reasoning = message
        .get("reasoning_content")
        .or_else(|| message.get("reasoning"))
        .and_then(|r| r.as_str())
        .map(|s| s.to_string());
    let tool_calls = message
        .get("tool_calls")
        .and_then(|tc| tc.as_array())
        .map(|tc| {
            tc.iter()
                .map(|call| {
                    let id = call
                        .get("id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let function = call.get("function").unwrap_or(&Value::Null);
                    let name = function
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let args_str = function
                        .get("arguments")
                        .and_then(|v| v.as_str())
                        .unwrap_or("{}")
                        .to_string();
                    let arguments = serde_json::from_str(&args_str).unwrap_or(json!({}));
                    json!({"id": id, "name": name, "arguments": arguments})
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let usage = resp.get("usage").map(usage_from_json);
    Ok(
        json!({"text": text, "tool_calls": tool_calls, "finish_reason": finish_reason, "reasoning": reasoning, "usage": usage, "extras": {}}),
    )
}

// -- Streaming SSE -----------------------------------------------------------

pub fn stream_openai_chat(
    req: &ProviderRequest,
    out: &mut impl Write,
    stream_id: &str,
) -> Result<Value, String> {
    let url = format!("{}/chat/completions", req.base_url.trim_end_matches('/'));
    let body = build_openai_chat_body(req, true);
    let resp = send_json(
        agent()
            .post(&url)
            .set("Authorization", &format!("Bearer {}", req.api_key))
            .set("Content-Type", "application/json"),
        body,
    )?;
    let reader = resp.into_reader();
    let buf = BufReader::new(reader);
    let mut text_parts: Vec<String> = Vec::new();
    let mut reasoning_parts: Vec<String> = Vec::new();
    let mut tool_calls: Vec<ToolCallAccum> = Vec::new();
    let mut finish_reason: Option<String> = None;
    let mut usage: Option<Value> = None;
    for line_res in buf.lines() {
        let line = line_res.map_err(|e| format!("SSE read error: {e}"))?;
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with(':') {
            continue;
        }
        if !trimmed.starts_with("data: ") {
            continue;
        }
        let payload = &trimmed[6..];
        if payload == "[DONE]" {
            break;
        }
        let chunk: Value = match serde_json::from_str(payload) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(u) = chunk.get("usage") {
            usage = Some(usage_from_json(u));
        }
        let choices = match chunk.get("choices").and_then(|c| c.as_array()) {
            Some(c) => c,
            None => continue,
        };
        let choice = match choices.first() {
            Some(c) => c,
            None => continue,
        };
        let delta = choice.get("delta").unwrap_or(&Value::Null);
        if let Some(content) = delta.get("content").and_then(|c| c.as_str()) {
            if !content.is_empty() {
                text_parts.push(content.to_string());
                let frame = json!({"ok": true, "stream": "delta", "stream_id": stream_id, "data": {"text_delta": content}});
                writeln!(out, "{frame}").ok();
                out.flush().ok();
            }
        }
        if let Some(r) = delta
            .get("reasoning_content")
            .or_else(|| delta.get("reasoning"))
            .and_then(|r| r.as_str())
        {
            if !r.is_empty() {
                reasoning_parts.push(r.to_string());
                let frame = json!({"ok": true, "stream": "delta", "stream_id": stream_id, "data": {"reasoning_delta": r}});
                writeln!(out, "{frame}").ok();
                out.flush().ok();
            }
        }
        if let Some(tcs) = delta.get("tool_calls").and_then(|t| t.as_array()) {
            for tc in tcs {
                let idx = tc.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
                while tool_calls.len() <= idx {
                    tool_calls.push(ToolCallAccum::default());
                }
                let accum = &mut tool_calls[idx];
                if let Some(id) = tc.get("id").and_then(|v| v.as_str()) {
                    accum.id = id.to_string();
                }
                if let Some(func) = tc.get("function") {
                    if let Some(name) = func.get("name").and_then(|v| v.as_str()) {
                        accum.name = name.to_string();
                    }
                    if let Some(args) = func.get("arguments").and_then(|v| v.as_str()) {
                        accum.args.push_str(args);
                    }
                }
            }
        }
        if let Some(fr) = choice.get("finish_reason").and_then(|f| f.as_str()) {
            finish_reason = Some(fr.to_string());
        }
    }
    let text = if text_parts.is_empty() {
        Value::Null
    } else {
        Value::String(text_parts.join(""))
    };
    let reasoning = if reasoning_parts.is_empty() {
        Value::Null
    } else {
        Value::String(reasoning_parts.join(""))
    };
    let tool_calls_json: Vec<Value> = tool_calls
        .iter()
        .map(|tc| {
            let arguments =
                serde_json::from_str(&tc.args).unwrap_or(json!({"_raw": tc.args.clone()}));
            json!({"id": tc.id.clone(), "name": tc.name.clone(), "arguments": arguments})
        })
        .collect();
    Ok(
        json!({"text": text, "tool_calls": tool_calls_json, "finish_reason": finish_reason, "reasoning": reasoning, "usage": usage, "extras": {}}),
    )
}

// -- Anthropic Messages (non-streaming) -------------------------------------

fn anthropic_headers(req: &ProviderRequest) -> Vec<(&str, String)> {
    vec![
        ("x-api-key", req.api_key.clone()),
        ("anthropic-version", "2023-06-01".to_string()),
        ("Content-Type", "application/json".to_string()),
    ]
}

fn build_anthropic_body(req: &ProviderRequest, stream: bool) -> Value {
    let mut body = json!({
        "model": req.model,
        "messages": req.messages,
        "max_tokens": 4096,
    });
    if stream {
        body["stream"] = json!(true);
    }
    if let Some(settings) = &req.settings {
        if let Some(obj) = settings.as_object() {
            if let Some(body_obj) = body.as_object_mut() {
                for (k, v) in obj {
                    body_obj.insert(k.clone(), v.clone());
                }
            }
        }
    }
    if let Some(tools) = &req.tools {
        body["tools"] = tools.clone();
    }
    body
}

fn anthropic_usage(u: &Value) -> Value {
    let input = u.get("input_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
    let output = u.get("output_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
    let cache_read = u
        .get("cache_read_input_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let cache_write = u
        .get("cache_creation_input_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    json!({
        "input": input,
        "output": output,
        "cache_read": cache_read,
        "cache_write": cache_write,
    })
}

fn map_stop_reason(reason: &str) -> &str {
    match reason {
        "end_turn" => "stop",
        "tool_use" => "tool_calls",
        "max_tokens" => "length",
        "stop_sequence" => "stop",
        _ => reason,
    }
}

pub fn complete_anthropic(req: &ProviderRequest) -> Result<Value, String> {
    let url = format!("{}/v1/messages", req.base_url.trim_end_matches('/'));
    let body = build_anthropic_body(req, false);
    let mut request = agent().post(&url);
    for (k, v) in anthropic_headers(req) {
        request = request.set(k, &v);
    }
    let resp = send_json(request, body)?;
    let resp_json: Value = resp.into_json().map_err(|e| format!("JSON parse: {e}"))?;
    parse_anthropic_response(&resp_json)
}

fn parse_anthropic_response(resp: &Value) -> Result<Value, String> {
    let content = resp
        .get("content")
        .and_then(|c| c.as_array())
        .ok_or("missing content array")?;
    let mut text_parts: Vec<String> = Vec::new();
    let mut reasoning_parts: Vec<String> = Vec::new();
    let mut tool_calls: Vec<Value> = Vec::new();
    for block in content {
        match block.get("type").and_then(|t| t.as_str()) {
            Some("text") => {
                if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                    text_parts.push(t.to_string());
                }
            }
            Some("tool_use") => {
                let id = block
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let name = block
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let input = block.get("input").cloned().unwrap_or(json!({}));
                tool_calls.push(json!({"id": id, "name": name, "arguments": input}));
            }
            Some("thinking") => {
                if let Some(t) = block.get("thinking").and_then(|t| t.as_str()) {
                    reasoning_parts.push(t.to_string());
                }
            }
            Some("redacted_thinking") => {
                reasoning_parts.push("[redacted]".to_string());
            }
            _ => {}
        }
    }
    let stop_reason = resp
        .get("stop_reason")
        .and_then(|s| s.as_str())
        .map(|s| map_stop_reason(s).to_string());
    let usage = resp.get("usage").map(anthropic_usage);
    let text = if text_parts.is_empty() {
        Value::Null
    } else {
        Value::String(text_parts.join(""))
    };
    let reasoning = if reasoning_parts.is_empty() {
        Value::Null
    } else {
        Value::String(reasoning_parts.join(""))
    };
    Ok(json!({
        "text": text,
        "tool_calls": tool_calls,
        "finish_reason": stop_reason,
        "reasoning": reasoning,
        "usage": usage,
        "extras": {},
    }))
}

// -- Anthropic Messages (streaming SSE) -------------------------------------

pub fn stream_anthropic(
    req: &ProviderRequest,
    out: &mut impl Write,
    stream_id: &str,
) -> Result<Value, String> {
    let url = format!("{}/v1/messages", req.base_url.trim_end_matches('/'));
    let body = build_anthropic_body(req, true);
    let mut request = agent().post(&url);
    for (k, v) in anthropic_headers(req) {
        request = request.set(k, &v);
    }
    let resp = send_json(request, body)?;
    let reader = resp.into_reader();
    let buf = BufReader::new(reader);
    let mut text_parts: Vec<String> = Vec::new();
    let mut reasoning_parts: Vec<String> = Vec::new();
    let mut tool_calls: Vec<ToolCallAccum> = Vec::new();
    let mut block_types: Vec<String> = Vec::new();
    let mut finish_reason: Option<String> = None;
    let mut usage: Option<Value> = None;
    for line_res in buf.lines() {
        let line = line_res.map_err(|e| format!("SSE read error: {e}"))?;
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with(':') {
            continue;
        }
        if trimmed.starts_with("event:") {
            continue;
        }
        if !trimmed.starts_with("data: ") {
            continue;
        }
        let payload = &trimmed[6..];
        let chunk: Value = match serde_json::from_str(payload) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let chunk_type = chunk.get("type").and_then(|t| t.as_str()).unwrap_or("");
        match chunk_type {
            "message_start" => {
                if let Some(msg) = chunk.get("message") {
                    if let Some(u) = msg.get("usage") {
                        usage = Some(anthropic_usage(u));
                    }
                }
            }
            "content_block_start" => {
                let idx = chunk.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
                while block_types.len() <= idx {
                    block_types.push(String::new());
                    tool_calls.push(ToolCallAccum::default());
                }
                if let Some(block) = chunk.get("content_block") {
                    let bt = block.get("type").and_then(|t| t.as_str()).unwrap_or("");
                    block_types[idx] = bt.to_string();
                    if bt == "tool_use" {
                        if let Some(id) = block.get("id").and_then(|v| v.as_str()) {
                            tool_calls[idx].id = id.to_string();
                        }
                        if let Some(name) = block.get("name").and_then(|v| v.as_str()) {
                            tool_calls[idx].name = name.to_string();
                        }
                    }
                }
            }
            "content_block_delta" => {
                let idx = chunk.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
                if let Some(delta) = chunk.get("delta") {
                    let dt = delta.get("type").and_then(|t| t.as_str()).unwrap_or("");
                    match dt {
                        "text_delta" => {
                            if let Some(t) = delta.get("text").and_then(|t| t.as_str()) {
                                if !t.is_empty() {
                                    text_parts.push(t.to_string());
                                    let frame = json!({"ok": true, "stream": "delta", "stream_id": stream_id, "data": {"text_delta": t}});
                                    writeln!(out, "{frame}").ok();
                                    out.flush().ok();
                                }
                            }
                        }
                        "thinking_delta" => {
                            if let Some(t) = delta.get("thinking").and_then(|t| t.as_str()) {
                                if !t.is_empty() {
                                    reasoning_parts.push(t.to_string());
                                    let frame = json!({"ok": true, "stream": "delta", "stream_id": stream_id, "data": {"reasoning_delta": t}});
                                    writeln!(out, "{frame}").ok();
                                    out.flush().ok();
                                }
                            }
                        }
                        "input_json_delta" => {
                            if let Some(pj) = delta.get("partial_json").and_then(|v| v.as_str()) {
                                if idx < tool_calls.len() {
                                    tool_calls[idx].args.push_str(pj);
                                }
                            }
                        }
                        "signature_delta" => {
                            // Signature deltas are not streamed to UI;
                            // they're part of the thinking block's signature.
                        }
                        _ => {}
                    }
                }
            }
            "message_delta" => {
                if let Some(d) = chunk.get("delta") {
                    if let Some(sr) = d.get("stop_reason").and_then(|s| s.as_str()) {
                        finish_reason = Some(map_stop_reason(sr).to_string());
                    }
                }
                if let Some(u) = chunk.get("usage") {
                    let prev = usage.clone().unwrap_or(json!({}));
                    let mut merged = prev;
                    if let Some(out_tok) = u.get("output_tokens").and_then(|v| v.as_i64()) {
                        if let Some(obj) = merged.as_object_mut() {
                            obj.insert("output".to_string(), json!(out_tok));
                        }
                    }
                    usage = Some(merged);
                }
            }
            "message_stop" => {
                break;
            }
            _ => {}
        }
    }
    let text = if text_parts.is_empty() {
        Value::Null
    } else {
        Value::String(text_parts.join(""))
    };
    let reasoning = if reasoning_parts.is_empty() {
        Value::Null
    } else {
        Value::String(reasoning_parts.join(""))
    };
    let tool_calls_json: Vec<Value> = tool_calls
        .iter()
        .filter(|tc| !tc.name.is_empty())
        .map(|tc| {
            let arguments =
                serde_json::from_str(&tc.args).unwrap_or(json!({"_raw": tc.args.clone()}));
            json!({"id": tc.id.clone(), "name": tc.name.clone(), "arguments": arguments})
        })
        .collect();
    Ok(json!({
        "text": text,
        "tool_calls": tool_calls_json,
        "finish_reason": finish_reason,
        "reasoning": reasoning,
        "usage": usage,
        "extras": {},
    }))
}

// -- OpenAI Responses (non-streaming) ---------------------------------------

fn build_openai_responses_body(req: &ProviderRequest, stream: bool) -> Value {
    let mut body = json!({
        "model": req.model,
        "input": req.messages,
        "store": false,
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"summary": "auto"},
    });
    if stream {
        body["stream"] = json!(true);
    }
    if let Some(tools) = &req.tools {
        body["tools"] = tools.clone();
    }
    if let Some(settings) = &req.settings {
        if let Some(obj) = settings.as_object() {
            if let Some(body_obj) = body.as_object_mut() {
                for (k, v) in obj {
                    body_obj.insert(k.clone(), v.clone());
                }
            }
        }
    }
    body
}

pub fn complete_openai_responses(req: &ProviderRequest) -> Result<Value, String> {
    let url = format!("{}/v1/responses", req.base_url.trim_end_matches('/'));
    let body = build_openai_responses_body(req, false);
    let resp = send_json(
        agent()
            .post(&url)
            .set("Authorization", &format!("Bearer {}", req.api_key))
            .set("Content-Type", "application/json"),
        body,
    )?;
    let resp_json: Value = resp.into_json().map_err(|e| format!("JSON parse: {e}"))?;
    parse_openai_responses_response(&resp_json)
}

fn parse_openai_responses_response(resp: &Value) -> Result<Value, String> {
    let items = resp
        .get("output")
        .and_then(|o| o.as_array())
        .cloned()
        .unwrap_or_default();
    let mut texts: Vec<String> = Vec::new();
    let mut summaries: Vec<String> = Vec::new();
    let mut tool_calls: Vec<Value> = Vec::new();
    for item in &items {
        let kind = item.get("type").and_then(|t| t.as_str());
        match kind {
            Some("message") => {
                if let Some(content) = item.get("content") {
                    if let Some(s) = content.as_str() {
                        texts.push(s.to_string());
                    } else if let Some(parts) = content.as_array() {
                        for part in parts {
                            if part.get("type").and_then(|t| t.as_str()) == Some("output_text") {
                                if let Some(t) = part.get("text").and_then(|t| t.as_str()) {
                                    texts.push(t.to_string());
                                }
                            }
                        }
                    }
                }
            }
            Some("reasoning") => {
                if let Some(summary) = item.get("summary") {
                    if let Some(parts) = summary.as_array() {
                        for part in parts {
                            let text = if let Some(s) = part.as_str() {
                                s.to_string()
                            } else if let Some(t) = part.get("text").and_then(|t| t.as_str()) {
                                t.to_string()
                            } else {
                                continue;
                            };
                            if !text.is_empty() {
                                summaries.push(text);
                            }
                        }
                    }
                }
            }
            Some("function_call") => {
                let id = item
                    .get("call_id")
                    .or_else(|| item.get("id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let name = item
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let arguments = item.get("arguments").cloned().unwrap_or(json!({}));
                tool_calls.push(json!({"id": id, "name": name, "arguments": arguments}));
            }
            _ => {}
        }
    }
    let incomplete = resp.get("incomplete_details").cloned().unwrap_or(json!({}));
    let finish_reason = if !tool_calls.is_empty() {
        "tool_calls".to_string()
    } else if incomplete.get("reason").and_then(|r| r.as_str()) == Some("max_output_tokens") {
        "length".to_string()
    } else {
        "stop".to_string()
    };
    let usage = resp.get("usage").map(usage_from_openai_responses);
    let text = if texts.is_empty() {
        Value::Null
    } else {
        Value::String(texts.join(""))
    };
    let reasoning = if summaries.is_empty() {
        Value::Null
    } else {
        Value::String(summaries.join(""))
    };
    Ok(json!({
        "text": text,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "reasoning": reasoning,
        "usage": usage,
        "extras": {},
    }))
}

fn usage_from_openai_responses(u: &Value) -> Value {
    let input = u.get("input_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
    let output = u.get("output_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
    json!({
        "input": input,
        "output": output,
        "cache_read": 0,
        "cache_write": 0,
    })
}

// -- OpenAI Responses (streaming SSE) ---------------------------------------

pub fn stream_openai_responses(
    req: &ProviderRequest,
    out: &mut impl Write,
    stream_id: &str,
) -> Result<Value, String> {
    let url = format!("{}/v1/responses", req.base_url.trim_end_matches('/'));
    let body = build_openai_responses_body(req, true);
    let resp = send_json(
        agent()
            .post(&url)
            .set("Authorization", &format!("Bearer {}", req.api_key))
            .set("Content-Type", "application/json"),
        body,
    )?;
    let reader = resp.into_reader();
    let buf = BufReader::new(reader);
    let mut text_parts: Vec<String> = Vec::new();
    let mut reasoning_parts: Vec<String> = Vec::new();
    let mut final_response: Option<Value> = None;
    let mut event_type: Option<String> = None;
    for line_res in buf.lines() {
        let line = line_res.map_err(|e| format!("SSE read error: {e}"))?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("event: ") {
            event_type = Some(rest.trim().to_string());
            continue;
        }
        if !trimmed.starts_with("data: ") {
            continue;
        }
        let payload = &trimmed[6..];
        let chunk: Value = match serde_json::from_str(payload) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let kind = event_type.clone().unwrap_or_else(|| {
            chunk
                .get("type")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string()
        });
        match kind.as_str() {
            "response.output_text.delta" => {
                if let Some(t) = chunk.get("delta").and_then(|d| d.as_str()) {
                    if !t.is_empty() {
                        text_parts.push(t.to_string());
                        let frame = json!({"ok": true, "stream": "delta", "stream_id": stream_id, "data": {"text_delta": t}});
                        writeln!(out, "{frame}").ok();
                        out.flush().ok();
                    }
                }
            }
            "response.reasoning_summary_text.delta" => {
                if let Some(t) = chunk.get("delta").and_then(|d| d.as_str()) {
                    if !t.is_empty() {
                        reasoning_parts.push(t.to_string());
                        let frame = json!({"ok": true, "stream": "delta", "stream_id": stream_id, "data": {"reasoning_delta": t}});
                        writeln!(out, "{frame}").ok();
                        out.flush().ok();
                    }
                }
            }
            "response.completed" | "response.incomplete" | "response.failed" => {
                if let Some(r) = chunk.get("response") {
                    final_response = Some(r.clone());
                }
            }
            _ => {}
        }
    }
    let result = if let Some(final_resp) = final_response {
        parse_openai_responses_response(&final_resp)?
    } else {
        let text = if text_parts.is_empty() {
            Value::Null
        } else {
            Value::String(text_parts.join(""))
        };
        let reasoning = if reasoning_parts.is_empty() {
            Value::Null
        } else {
            Value::String(reasoning_parts.join(""))
        };
        json!({
            "text": text,
            "tool_calls": [],
            "finish_reason": Value::Null,
            "reasoning": reasoning,
            "usage": Value::Null,
            "extras": {},
        })
    };
    Ok(result)
}

// -- Dispatch ---------------------------------------------------------------

pub fn complete(req: &ProviderRequest) -> Result<Value, String> {
    match req.protocol.as_str() {
        "openai_chat" => complete_openai_chat(req),
        "anthropic" => complete_anthropic(req),
        "openai_responses" => complete_openai_responses(req),
        _ => Err(format!(
            "unknown protocol: {} (supports openai_chat, anthropic, openai_responses)",
            req.protocol
        )),
    }
}

pub fn stream(
    req: &ProviderRequest,
    out: &mut impl Write,
    stream_id: &str,
) -> Result<Value, String> {
    match req.protocol.as_str() {
        "openai_chat" => stream_openai_chat(req, out, stream_id),
        "anthropic" => stream_anthropic(req, out, stream_id),
        "openai_responses" => stream_openai_responses(req, out, stream_id),
        _ => Err(format!(
            "unknown protocol: {} (supports openai_chat, anthropic, openai_responses)",
            req.protocol
        )),
    }
}

// -- Capabilities (matrix + heuristics) ------------------------------------

#[allow(dead_code)]
struct MatrixEntry {
    id: &'static str,
    tools: bool,
    vision: bool,
    pdf: bool,
    parallel_tool_calls: bool,
    streaming: bool,
    context_window: Option<i64>,
}

const AGENTIC: MatrixEntry = MatrixEntry {
    id: "",
    tools: true,
    vision: false,
    pdf: false,
    parallel_tool_calls: true,
    streaming: true,
    context_window: None,
};
const AGENTIC_VISION: MatrixEntry = MatrixEntry {
    id: "",
    tools: true,
    vision: true,
    pdf: true,
    parallel_tool_calls: true,
    streaming: true,
    context_window: None,
};

const MATRIX: &[MatrixEntry] = &[
    MatrixEntry {
        id: "gpt-5.6-sol",
        context_window: Some(400_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "gpt-5.6-terra",
        context_window: Some(400_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "gpt-5.6-luna",
        context_window: Some(400_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "gpt-5.5",
        context_window: Some(400_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "anthropic:claude-fable-5",
        context_window: Some(1_000_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "anthropic:claude-opus-4-8",
        context_window: Some(200_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "anthropic:claude-sonnet-4-6",
        context_window: Some(200_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "anthropic:claude-haiku-4-5",
        context_window: Some(200_000),
        ..AGENTIC_VISION
    },
    MatrixEntry {
        id: "meta:muse-spark-1.1",
        tools: true,
        vision: true,
        pdf: false,
        parallel_tool_calls: true,
        streaming: true,
        context_window: None,
    },
    MatrixEntry {
        id: "zai:glm-5.2",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "deepseek:deepseek-v4-flash",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "deepseek:deepseek-v4-pro",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "kimi:kimi-k2.6",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "minimax:MiniMax-M2.5",
        ..AGENTIC
    },
    MatrixEntry {
        id: "qwen:qwen3-max",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "xai:grok-4.3",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "mistral:mistral-large-latest",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "together:thinkingmachines/Inkling",
        ..AGENTIC
    },
    MatrixEntry {
        id: "together:zai-org/GLM-5.2",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "together:moonshotai/Kimi-K3",
        tools: true,
        vision: true,
        pdf: false,
        parallel_tool_calls: true,
        streaming: true,
        context_window: Some(1_000_000),
    },
    MatrixEntry {
        id: "together:moonshotai/Kimi-K2.7-Code",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "together:moonshotai/Kimi-K2.6",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "together:deepseek-ai/DeepSeek-V4-Pro",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        context_window: Some(1_000_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "fireworks:accounts/fireworks/models/glm-5p2",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "fireworks:accounts/fireworks/models/kimi-k2p6",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "fireworks:accounts/fireworks/models/deepseek-v4-pro",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "fireworks:accounts/fireworks/models/llama4-maverick-instruct-basic",
        context_window: Some(1_000_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "openrouter:z-ai/glm-5.2",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "openrouter:moonshotai/kimi-k2.6",
        context_window: Some(256_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "openrouter:deepseek/deepseek-v4-pro",
        context_window: Some(128_000),
        ..AGENTIC
    },
    MatrixEntry {
        id: "openrouter:meta-llama/llama-4-maverick",
        context_window: Some(1_000_000),
        ..AGENTIC
    },
];

fn caps_json(tools: bool, vision: bool, pdf: bool, parallel: bool, streaming: bool) -> Value {
    json!({
        "tools": tools,
        "vision": vision,
        "pdf": pdf,
        "parallel_tool_calls": parallel,
        "streaming": streaming,
    })
}

pub fn capabilities_for(model: &str) -> Value {
    if let Some(entry) = MATRIX.iter().find(|e| e.id == model) {
        return caps_json(
            entry.tools,
            entry.vision,
            entry.pdf,
            entry.parallel_tool_calls,
            entry.streaming,
        );
    }
    let (provider, name) = if let Some((p, n)) = model.split_once(':') {
        (p.to_lowercase(), n.to_lowercase())
    } else {
        (String::new(), model.to_lowercase())
    };
    if provider == "anthropic" {
        return caps_json(true, true, true, true, true);
    }
    if name.starts_with("gpt-5") || name.starts_with("gpt-4") {
        return caps_json(true, true, true, true, true);
    }
    if name.starts_with("o1") || name.starts_with("o3") || name.starts_with("o4") {
        return caps_json(true, false, false, false, true);
    }
    if name.starts_with("deepseek")
        || name.starts_with("glm")
        || name.starts_with("kimi")
        || name.starts_with("minimax")
        || name.starts_with("qwen")
        || name.starts_with("grok")
        || name.starts_with("mistral")
        || name.starts_with("magistral")
    {
        return caps_json(true, false, false, true, true);
    }
    caps_json(true, false, false, false, true)
}

// -- Endpoint caps (stateful: endpoint_caps.json) ---------------------------

pub fn endpoint_caps_read(path: &str, endpoint_key: &str) -> Value {
    let p = std::path::PathBuf::from(path);
    let store: Value = match std::fs::read_to_string(&p) {
        Ok(s) => serde_json::from_str(&s).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    store.get(endpoint_key).cloned().unwrap_or(json!({}))
}

pub fn endpoint_reject(path: &str, endpoint_key: &str, field: &str) -> Value {
    let p = std::path::PathBuf::from(path);
    let mut store: Value = match std::fs::read_to_string(&p) {
        Ok(s) => serde_json::from_str(&s).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    if !store.is_object() {
        store = json!({});
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let entry = store
        .as_object_mut()
        .unwrap()
        .entry(endpoint_key.to_string())
        .or_insert(json!({}));
    if let Some(obj) = entry.as_object_mut() {
        obj.insert(field.to_string(), json!(false));
        obj.insert("updated_at".to_string(), json!(now));
    }
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(&p, serde_json::to_string_pretty(&store).unwrap_or_default());
    json!({"ok": true})
}

// -- Health (stateful: provider_health.json) -------------------------------

const HEALTH_MAX_SAMPLES: usize = 200;
const HEALTH_MAX_AGE_SECS: f64 = 86400.0;

pub fn health_record(
    path: &str,
    endpoint: &str,
    model: &str,
    ok: bool,
    ttft_ms: Option<f64>,
    duration_ms: Option<f64>,
    error_class: Option<&str>,
) -> Value {
    let p = std::path::PathBuf::from(path);
    let mut store: Value = match std::fs::read_to_string(&p) {
        Ok(s) => serde_json::from_str(&s).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    if !store.is_object() {
        store = json!({});
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let store_obj = store.as_object_mut().unwrap();
    let bucket = store_obj.entry(endpoint.to_string()).or_insert(json!({}));
    if !bucket.is_object() {
        *bucket = json!({});
    }
    let bucket_obj = bucket.as_object_mut().unwrap();
    let row = bucket_obj
        .entry(model.to_string())
        .or_insert(json!({"samples": 0, "errors": 0, "ttft_ms": [], "duration_ms": [], "last_error_class": null, "last_ts": 0.0}));
    if !row.is_object() {
        *row = json!({"samples": 0, "errors": 0, "ttft_ms": [], "duration_ms": [], "last_error_class": null, "last_ts": 0.0});
    }
    let row_obj = row.as_object_mut().unwrap();
    let samples = row_obj.get("samples").and_then(|v| v.as_i64()).unwrap_or(0) + 1;
    row_obj.insert("samples".to_string(), json!(samples));
    if !ok {
        let errors = row_obj.get("errors").and_then(|v| v.as_i64()).unwrap_or(0) + 1;
        row_obj.insert("errors".to_string(), json!(errors));
        row_obj.insert("last_error_class".to_string(), json!(error_class));
    }
    // Cap rolling buffers.
    if let Some(ttft) = ttft_ms {
        let mut arr = row_obj
            .get("ttft_ms")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        arr.push(json!(ttft));
        if arr.len() > HEALTH_MAX_SAMPLES {
            arr = arr[arr.len() - HEALTH_MAX_SAMPLES..].to_vec();
        }
        row_obj.insert("ttft_ms".to_string(), Value::Array(arr));
    }
    if let Some(dur) = duration_ms {
        let mut arr = row_obj
            .get("duration_ms")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        arr.push(json!(dur));
        if arr.len() > HEALTH_MAX_SAMPLES {
            arr = arr[arr.len() - HEALTH_MAX_SAMPLES..].to_vec();
        }
        row_obj.insert("duration_ms".to_string(), Value::Array(arr));
    }
    row_obj.insert("last_ts".to_string(), json!(now));
    // Drop stale entries for this endpoint.
    let to_drop: Vec<String> = bucket_obj
        .iter()
        .filter_map(|(m, other)| {
            if m == model {
                return None;
            }
            let ts = other.get("last_ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
            if now - ts > HEALTH_MAX_AGE_SECS {
                Some(m.clone())
            } else {
                None
            }
        })
        .collect();
    for m in to_drop {
        bucket_obj.remove(&m);
    }
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(&p, serde_json::to_string_pretty(&store).unwrap_or_default());
    json!({"ok": true})
}

pub fn health_profile(path: &str, endpoint: &str, model: &str) -> Value {
    let p = std::path::PathBuf::from(path);
    let store: Value = match std::fs::read_to_string(&p) {
        Ok(s) => serde_json::from_str(&s).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    let row = store
        .get(endpoint)
        .and_then(|b| b.get(model))
        .cloned()
        .unwrap_or(json!({}));
    row
}

pub fn health_all(path: &str) -> Value {
    let p = std::path::PathBuf::from(path);
    let store: Value = match std::fs::read_to_string(&p) {
        Ok(s) => serde_json::from_str(&s).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    store
}

// -- Routing (provider name resolution) -------------------------------------

pub fn route(model: &str, providers: &[String], default: &str) -> Value {
    if let Some((prefix, rest)) = model.split_once(':') {
        if providers.iter().any(|p| p == prefix) {
            return json!({"provider": prefix, "bare": rest});
        }
    }
    json!({"provider": default, "bare": model})
}
