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
    let resp = agent()
        .post(&url)
        .set("Authorization", &format!("Bearer {}", req.api_key))
        .set("Content-Type", "application/json")
        .send_json(body)
        .map_err(|e| format!("HTTP error: {e}"))?;
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
    let resp = agent()
        .post(&url)
        .set("Authorization", &format!("Bearer {}", req.api_key))
        .set("Content-Type", "application/json")
        .send_json(body)
        .map_err(|e| format!("HTTP error: {e}"))?;
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

// -- Dispatch ---------------------------------------------------------------

pub fn complete(req: &ProviderRequest) -> Result<Value, String> {
    match req.protocol.as_str() {
        "openai_chat" => complete_openai_chat(req),
        _ => Err(format!(
            "unknown protocol: {} (Phase 1 supports openai_chat)",
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
        _ => Err(format!(
            "unknown protocol: {} (Phase 1 supports openai_chat)",
            req.protocol
        )),
    }
}
