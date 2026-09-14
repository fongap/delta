//! Rust authority for model routing, provider profiles, settings and secrets.
//!
//! Product surfaces submit only a model id. This module resolves that id to a
//! protocol, endpoint and credential inside the Rust boundary. Secret values
//! are accepted on writes and consumed by the provider transport, but are never
//! included in settings/provider responses.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::RuntimeConfig;

const DEFAULT_OPENAI_URL: &str = "https://api.openai.com/v1";
const DEFAULT_ANTHROPIC_URL: &str = "https://api.anthropic.com";
const OPENAI_PROTOCOL: &str = "openai";
const ANTHROPIC_PROTOCOL: &str = "anthropic";
const PROFILE_PREFIX: &str = "provider-profile:";

const MODEL_MATRIX: &[(&str, &str, Option<u64>)] = &[
    ("gpt-5.6-sol", "GPT-5.6 Sol · OpenAI", Some(400_000)),
    ("gpt-5.6-terra", "GPT-5.6 Terra · OpenAI", Some(400_000)),
    ("gpt-5.6-luna", "GPT-5.6 Luna · OpenAI", Some(400_000)),
    ("gpt-5.5", "GPT-5.5 · OpenAI", Some(400_000)),
    (
        "anthropic:claude-fable-5",
        "Claude Fable 5 · Anthropic",
        Some(1_000_000),
    ),
    (
        "anthropic:claude-opus-4-8",
        "Claude Opus 4.8 · Anthropic",
        Some(200_000),
    ),
    (
        "anthropic:claude-sonnet-4-6",
        "Claude Sonnet 4.6 · Anthropic",
        Some(200_000),
    ),
    (
        "anthropic:claude-haiku-4-5",
        "Claude Haiku 4.5 · Anthropic",
        Some(200_000),
    ),
    ("meta:muse-spark-1.1", "Muse Spark 1.1 · Meta", None),
    ("zai:glm-5.2", "GLM-5.2 · Z AI", Some(128_000)),
    (
        "deepseek:deepseek-v4-flash",
        "DeepSeek V4 Flash · DeepSeek",
        Some(128_000),
    ),
    (
        "deepseek:deepseek-v4-pro",
        "DeepSeek V4 Pro · DeepSeek",
        Some(128_000),
    ),
    ("kimi:kimi-k2.6", "Kimi K2.6 · Moonshot", Some(256_000)),
    ("minimax:MiniMax-M2.5", "MiniMax M2.5 · MiniMax", None),
    ("qwen:qwen3-max", "Qwen3 Max · Alibaba", Some(256_000)),
    ("xai:grok-4.3", "Grok 4.3 · xAI", Some(256_000)),
    (
        "mistral:mistral-large-latest",
        "Mistral Large · Mistral",
        Some(128_000),
    ),
    (
        "together:thinkingmachines/Inkling",
        "Inkling · via Together",
        None,
    ),
    (
        "together:zai-org/GLM-5.2",
        "GLM-5.2 · via Together",
        Some(128_000),
    ),
    (
        "together:moonshotai/Kimi-K3",
        "Kimi K3 · via Together",
        Some(1_000_000),
    ),
    (
        "together:moonshotai/Kimi-K2.7-Code",
        "Kimi K2.7 Code · via Together",
        Some(256_000),
    ),
    (
        "together:moonshotai/Kimi-K2.6",
        "Kimi K2.6 · via Together",
        Some(256_000),
    ),
    (
        "together:deepseek-ai/DeepSeek-V4-Pro",
        "DeepSeek V4 Pro · via Together",
        Some(128_000),
    ),
    (
        "together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "Llama 4 Maverick · via Together",
        Some(1_000_000),
    ),
    (
        "fireworks:accounts/fireworks/models/glm-5p2",
        "GLM-5.2 · via Fireworks",
        Some(128_000),
    ),
    (
        "fireworks:accounts/fireworks/models/kimi-k2p6",
        "Kimi K2.6 · via Fireworks",
        Some(256_000),
    ),
    (
        "fireworks:accounts/fireworks/models/deepseek-v4-pro",
        "DeepSeek V4 Pro · via Fireworks",
        Some(128_000),
    ),
    (
        "fireworks:accounts/fireworks/models/llama4-maverick-instruct-basic",
        "Llama 4 Maverick · via Fireworks",
        Some(1_000_000),
    ),
    (
        "openrouter:z-ai/glm-5.2",
        "GLM-5.2 · via OpenRouter",
        Some(128_000),
    ),
    (
        "openrouter:moonshotai/kimi-k2.6",
        "Kimi K2.6 · via OpenRouter",
        Some(256_000),
    ),
    (
        "openrouter:deepseek/deepseek-v4-pro",
        "DeepSeek V4 Pro · via OpenRouter",
        Some(128_000),
    ),
    (
        "openrouter:meta-llama/llama-4-maverick",
        "Llama 4 Maverick · via OpenRouter",
        Some(1_000_000),
    ),
];

#[derive(Clone, Copy)]
struct ProviderSpec {
    name: &'static str,
    title: &'static str,
    protocol: &'static str,
    base_url: &'static str,
    recommended_model: &'static str,
    env_key: &'static str,
    blurb: &'static str,
}

const PROVIDERS: &[ProviderSpec] = &[
    ProviderSpec { name: "openai", title: "OpenAI", protocol: OPENAI_PROTOCOL, base_url: DEFAULT_OPENAI_URL, recommended_model: "gpt-5.6-sol", env_key: "OPENAI_API_KEY", blurb: "OpenAI Responses API by default; Chat Completions is available for compatible endpoints." },
    ProviderSpec { name: "anthropic", title: "Claude (Anthropic)", protocol: ANTHROPIC_PROTOCOL, base_url: DEFAULT_ANTHROPIC_URL, recommended_model: "claude-fable-5", env_key: "ANTHROPIC_API_KEY", blurb: "Anthropic-compatible Messages API." },
    ProviderSpec { name: "zai", title: "Z AI (GLM)", protocol: OPENAI_PROTOCOL, base_url: "https://api.z.ai/api/paas/v4", recommended_model: "glm-5.2", env_key: "ZAI_API_KEY", blurb: "Uses Z AI's OpenAI-compatible API." },
    ProviderSpec { name: "deepseek", title: "DeepSeek", protocol: OPENAI_PROTOCOL, base_url: "https://api.deepseek.com", recommended_model: "deepseek-v4-flash", env_key: "DEEPSEEK_API_KEY", blurb: "Uses DeepSeek's OpenAI-compatible API." },
    ProviderSpec { name: "kimi", title: "Kimi (Moonshot AI)", protocol: OPENAI_PROTOCOL, base_url: "https://api.moonshot.ai/v1", recommended_model: "kimi-k2.6", env_key: "MOONSHOT_API_KEY", blurb: "Uses Moonshot's OpenAI-compatible API." },
    ProviderSpec { name: "minimax", title: "MiniMax", protocol: OPENAI_PROTOCOL, base_url: "https://api.minimax.io/v1", recommended_model: "MiniMax-M2.5", env_key: "MINIMAX_API_KEY", blurb: "Uses MiniMax's OpenAI-compatible API." },
    ProviderSpec { name: "qwen", title: "Qwen (Alibaba)", protocol: OPENAI_PROTOCOL, base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", recommended_model: "qwen3-max", env_key: "DASHSCOPE_API_KEY", blurb: "Uses Alibaba Model Studio's OpenAI-compatible API." },
    ProviderSpec { name: "xai", title: "xAI (Grok)", protocol: OPENAI_PROTOCOL, base_url: "https://api.x.ai/v1", recommended_model: "grok-4.3", env_key: "XAI_API_KEY", blurb: "Uses xAI's OpenAI-compatible API." },
    ProviderSpec { name: "mistral", title: "Mistral", protocol: OPENAI_PROTOCOL, base_url: "https://api.mistral.ai/v1", recommended_model: "mistral-large-latest", env_key: "MISTRAL_API_KEY", blurb: "Uses Mistral's OpenAI-compatible API." },
    ProviderSpec { name: "meta", title: "Meta (Muse Spark)", protocol: OPENAI_PROTOCOL, base_url: "https://api.meta.ai/v1", recommended_model: "muse-spark-1.1", env_key: "META_API_KEY", blurb: "Uses Meta's OpenAI-compatible Model API." },
    ProviderSpec { name: "together", title: "Together AI", protocol: OPENAI_PROTOCOL, base_url: "https://api.together.xyz/v1", recommended_model: "zai-org/GLM-5.2", env_key: "TOGETHER_API_KEY", blurb: "Uses Together AI's OpenAI-compatible API." },
    ProviderSpec { name: "fireworks", title: "Fireworks AI", protocol: OPENAI_PROTOCOL, base_url: "https://api.fireworks.ai/inference/v1", recommended_model: "accounts/fireworks/models/glm-5p2", env_key: "FIREWORKS_API_KEY", blurb: "Uses Fireworks AI's OpenAI-compatible API." },
    ProviderSpec { name: "openrouter", title: "OpenRouter", protocol: OPENAI_PROTOCOL, base_url: "https://openrouter.ai/api/v1", recommended_model: "z-ai/glm-5.2", env_key: "OPENROUTER_API_KEY", blurb: "Uses OpenRouter's OpenAI-compatible API." },
];

#[derive(Clone)]
struct ProviderDescriptor {
    name: String,
    title: String,
    protocol: String,
    base_url: String,
    recommended_model: String,
    env_key: String,
    blurb: String,
}

impl From<ProviderSpec> for ProviderDescriptor {
    fn from(value: ProviderSpec) -> Self {
        Self {
            name: value.name.to_string(),
            title: value.title.to_string(),
            protocol: value.protocol.to_string(),
            base_url: value.base_url.to_string(),
            recommended_model: value.recommended_model.to_string(),
            env_key: value.env_key.to_string(),
            blurb: value.blurb.to_string(),
        }
    }
}

#[derive(Clone)]
pub struct ModelAuthority {
    state_dir: PathBuf,
}

impl ModelAuthority {
    pub fn new(state_dir: impl Into<PathBuf>) -> Self {
        Self {
            state_dir: state_dir.into(),
        }
    }

    pub fn resolve_runtime_config(&self, requested_model: &str) -> Result<RuntimeConfig, String> {
        let prefs = self.read_prefs();
        let model_id = if requested_model.trim().is_empty() {
            prefs
                .get("default_model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
        } else {
            requested_model.trim()
        };
        if model_id.is_empty() {
            return Err("no model configured".to_string());
        }
        let (provider_name, provider_model) = self.route_model(model_id, &prefs);
        let descriptor = self.descriptor(&provider_name, &prefs)?;
        let profile = self.resolved_profile(&provider_name, &prefs);
        let profile_protocol = profile
            .get("protocol")
            .and_then(Value::as_str)
            .unwrap_or(&descriptor.protocol);
        let base_url = profile
            .get("base_url")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .unwrap_or(&descriptor.base_url)
            .trim()
            .trim_end_matches('/')
            .to_string();
        let api_key = self.profile_api_key(&descriptor, &profile, &base_url);
        if api_key.is_empty() {
            return Err(format!("provider {} is not configured", descriptor.name));
        }
        let protocol = match profile_protocol {
            ANTHROPIC_PROTOCOL => "anthropic",
            OPENAI_PROTOCOL => {
                let mode = profile.get("api_mode").and_then(Value::as_str).unwrap_or(
                    if descriptor.name == "openai" {
                        "responses"
                    } else {
                        "chat"
                    },
                );
                if mode == "responses" {
                    "openai_responses"
                } else {
                    "openai_chat"
                }
            }
            other => return Err(format!("unsupported provider protocol: {other}")),
        };
        let model_settings = prefs
            .get("model_settings")
            .and_then(Value::as_object)
            .and_then(|settings| settings.get(model_id))
            .cloned()
            .unwrap_or_else(|| json!({}));
        Ok(RuntimeConfig {
            model_id: model_id.to_string(),
            model: provider_model,
            protocol: protocol.to_string(),
            api_key,
            base_url,
            model_settings,
            ..RuntimeConfig::default()
        })
    }

    pub fn settings(&self) -> Value {
        let prefs = self.read_prefs();
        let default_model = prefs
            .get("default_model")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let hidden: HashSet<&str> = prefs
            .get("hidden_models")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        let mut models: Vec<String> = Vec::new();
        if !default_model.is_empty() {
            models.push(default_model.clone());
        }
        for (model, _, _) in MODEL_MATRIX {
            if !hidden.contains(model)
                && self.provider_configured(&self.route_model(model, &prefs).0, &prefs)
                && !models.iter().any(|existing| existing == model)
            {
                models.push((*model).to_string());
            }
        }
        for model in prefs
            .get("models")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if !hidden.contains(model)
                && self.provider_configured(&self.route_model(model, &prefs).0, &prefs)
                && !models.iter().any(|existing| existing == model)
            {
                models.push(model.to_string());
            }
        }
        let openai_profile = self.resolved_profile("openai", &prefs);
        let openai_descriptor = self.builtin_descriptor("openai").unwrap();
        let openai_base = openai_profile
            .get("base_url")
            .and_then(Value::as_str)
            .unwrap_or(&openai_descriptor.base_url);
        let env_key = self.env_key_allowed(&openai_descriptor, openai_base)
            && std::env::var(openai_descriptor.env_key).is_ok_and(|key| !key.trim().is_empty());
        let stored_key = openai_profile
            .get("api_key")
            .and_then(Value::as_str)
            .is_some_and(|key| !key.trim().is_empty());
        let labels: Map<String, Value> = MODEL_MATRIX
            .iter()
            .map(|(id, label, _)| ((*id).to_string(), json!(label)))
            .collect();
        let windows: Map<String, Value> = MODEL_MATRIX
            .iter()
            .filter_map(|(id, _, window)| window.map(|value| ((*id).to_string(), json!(value))))
            .collect();
        json!({
            "provider": "openai",
            "model": default_model,
            "models": models,
            "model_labels": labels,
            "model_context_windows": windows,
            "has_key": env_key || stored_key,
            "model_ready": !default_model.is_empty()
                && self.provider_configured(&self.route_model(&default_model, &prefs).0, &prefs),
            "source": if env_key { Value::String("env".to_string()) } else if stored_key { Value::String("store".to_string()) } else { Value::Null },
            "onboarded": prefs.get("onboarded").and_then(Value::as_bool).unwrap_or(false),
            "language": prefs.get("language").cloned().unwrap_or(Value::Null),
            "sessions_peek": bounded_i64(prefs.get("sessions_peek"), 5, 1, 50),
            "context_bar": prefs.get("context_bar").and_then(Value::as_bool).unwrap_or(false),
            "scratch_base": prefs.get("scratch_base").and_then(Value::as_str).unwrap_or("~/Delta"),
            "secrets_path": self.secrets_path().to_string_lossy(),
            "pdf_fallback": match prefs.get("pdf_fallback").and_then(Value::as_str) { Some("images") => "images", _ => "text" },
            "pdf_max_pages": bounded_i64(prefs.get("pdf_max_pages"), 20, 1, 100),
            "pdf_max_mb": bounded_i64(prefs.get("pdf_max_mb"), 10, 1, 10),
            "compaction_threshold_pct": bounded_f64(prefs.get("compaction_threshold_pct"), 0.8, 0.10, 0.95),
            "compaction_cap_tokens": bounded_i64(prefs.get("compaction_cap_tokens"), 250_000, 10_000, 2_000_000),
            "compaction_model": prefs.get("compaction_model").and_then(Value::as_str).unwrap_or(""),
        })
    }

    pub fn protocols(&self) -> Value {
        json!([
            {
                "id": OPENAI_PROTOCOL,
                "title": "OpenAI-compatible",
                "needs_key": false,
                "fields": openai_fields("", false),
                "recommended_model": "gpt-4o",
                "env_key": Value::Null,
                "blurb": "OpenAI-compatible Chat Completions or Responses API",
            },
            {
                "id": ANTHROPIC_PROTOCOL,
                "title": "Anthropic-compatible",
                "needs_key": true,
                "fields": anthropic_fields(""),
                "recommended_model": "claude-fable-5",
                "env_key": Value::Null,
                "blurb": "Anthropic-compatible Messages API",
            }
        ])
    }

    pub fn providers(&self) -> Value {
        let prefs = self.read_prefs();
        let mut rows: Vec<Value> = PROVIDERS
            .iter()
            .map(|descriptor| self.provider_row((*descriptor).into(), false, &prefs))
            .collect();
        if let Some(custom) = prefs.get("provider_profiles").and_then(Value::as_object) {
            for (name, meta) in custom {
                if PROVIDERS.iter().any(|descriptor| descriptor.name == name) {
                    continue;
                }
                let protocol = meta
                    .get("protocol")
                    .and_then(Value::as_str)
                    .unwrap_or(OPENAI_PROTOCOL);
                let descriptor = ProviderDescriptor {
                    name: name.clone(),
                    title: name.clone(),
                    protocol: if protocol == ANTHROPIC_PROTOCOL {
                        ANTHROPIC_PROTOCOL.to_string()
                    } else {
                        OPENAI_PROTOCOL.to_string()
                    },
                    base_url: if protocol == ANTHROPIC_PROTOCOL {
                        DEFAULT_ANTHROPIC_URL.to_string()
                    } else {
                        DEFAULT_OPENAI_URL.to_string()
                    },
                    recommended_model: if protocol == ANTHROPIC_PROTOCOL {
                        "claude-fable-5".to_string()
                    } else {
                        "gpt-4o".to_string()
                    },
                    env_key: String::new(),
                    blurb: "User-defined provider".to_string(),
                };
                rows.push(self.provider_row(descriptor, true, &prefs));
            }
        }
        Value::Array(rows)
    }

    pub fn set_provider(
        &self,
        name: &str,
        protocol: Option<&str>,
        fields: &Value,
    ) -> Result<Value, String> {
        let name = name.trim();
        if name.is_empty() {
            return Err("name required".to_string());
        }
        let mut prefs = self.read_prefs();
        if let Some(protocol) = protocol {
            validate_alias(name)?;
            if PROVIDERS.iter().any(|descriptor| descriptor.name == name) {
                return Err(format!("provider already exists: {name}"));
            }
            validate_protocol(protocol)?;
            let profiles = object_mut(&mut prefs, "provider_profiles");
            profiles.insert(
                name.to_string(),
                json!({"protocol": protocol, "preset": false}),
            );
        }
        let descriptor = self.descriptor(name, &prefs)?;
        let mut secrets = self.read_secrets();
        let key = profile_key(name);
        let mut profile = secrets
            .get(&key)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        profile
            .entry("name".to_string())
            .or_insert_with(|| json!(name));
        profile
            .entry("protocol".to_string())
            .or_insert_with(|| json!(descriptor.protocol));
        profile
            .entry("base_url".to_string())
            .or_insert_with(|| json!(descriptor.base_url));
        if descriptor.protocol == OPENAI_PROTOCOL {
            profile.entry("api_mode".to_string()).or_insert_with(|| {
                json!(if name == "openai" {
                    "responses"
                } else {
                    "chat"
                })
            });
        }
        if let Some(fields) = fields.as_object() {
            for field in ["api_key", "base_url", "api_mode"] {
                if let Some(value) = fields.get(field).and_then(Value::as_str) {
                    let value = value.trim();
                    if !value.is_empty() {
                        profile.insert(field.to_string(), json!(value));
                    }
                }
            }
            if fields
                .get("api_key")
                .and_then(Value::as_str)
                .is_some_and(|value| !value.trim().is_empty())
            {
                profile.insert(
                    "key_set_at".to_string(),
                    json!(time::OffsetDateTime::now_utc().date().to_string()),
                );
            }
        }
        validate_profile(&descriptor, &profile, protocol.is_none())?;
        secrets.insert(key, Value::Object(profile));
        self.write_secrets(&secrets)?;

        let recommended = if name == "openai" {
            descriptor.recommended_model.to_string()
        } else {
            format!("{name}:{}", descriptor.recommended_model)
        };
        self.add_model_to_prefs(&mut prefs, &recommended);
        let current = prefs
            .get("default_model")
            .and_then(Value::as_str)
            .unwrap_or("");
        if current.is_empty()
            || !self.provider_configured(&self.route_model(current, &prefs).0, &prefs)
        {
            prefs.insert("default_model".to_string(), json!(recommended));
        }
        self.write_prefs(&prefs)?;
        Ok(json!({
            "ok": true,
            "provider": name,
            "protocol": descriptor.protocol,
            "recommended_model": descriptor.recommended_model,
        }))
    }

    pub fn remove_provider(&self, name: &str) -> Result<Value, String> {
        let mut prefs = self.read_prefs();
        let custom = prefs
            .get("provider_profiles")
            .and_then(Value::as_object)
            .is_some_and(|profiles| profiles.contains_key(name));
        if !custom && self.builtin_descriptor(name).is_none() {
            return Err(format!("unknown provider: {name}"));
        }
        let mut secrets = self.read_secrets();
        secrets.remove(&profile_key(name));
        self.write_secrets(&secrets)?;
        if custom {
            if let Some(profiles) = prefs
                .get_mut("provider_profiles")
                .and_then(Value::as_object_mut)
            {
                profiles.remove(name);
            }
            let prefix = format!("{name}:");
            retain_strings_without_prefix(&mut prefs, "models", &prefix);
            retain_strings_without_prefix(&mut prefs, "hidden_models", &prefix);
            self.write_prefs(&prefs)?;
        }
        Ok(json!({"ok": true, "provider": name}))
    }

    pub fn set_default_model(&self, model: &str) -> Result<Value, String> {
        let model = model.trim();
        if model.is_empty() {
            return Err("empty model".to_string());
        }
        let mut prefs = self.read_prefs();
        self.add_model_to_prefs(&mut prefs, model);
        prefs.insert("default_model".to_string(), json!(model));
        self.write_prefs(&prefs)?;
        Ok(with_ok(self.settings()))
    }

    pub fn add_model(&self, model: &str) -> Result<Value, String> {
        let model = model.trim();
        if model.is_empty() {
            return Err("empty model".to_string());
        }
        let mut prefs = self.read_prefs();
        self.add_model_to_prefs(&mut prefs, model);
        self.write_prefs(&prefs)?;
        Ok(with_ok(self.settings()))
    }

    pub fn remove_model(&self, model: &str) -> Result<Value, String> {
        let model = model.trim();
        let mut prefs = self.read_prefs();
        if prefs.get("default_model").and_then(Value::as_str) == Some(model) {
            return Err("default model cannot be hidden".to_string());
        }
        remove_string(&mut prefs, "models", model);
        if MODEL_MATRIX.iter().any(|(id, _, _)| *id == model) {
            push_unique(&mut prefs, "hidden_models", model);
        }
        self.write_prefs(&prefs)?;
        Ok(with_ok(self.settings()))
    }

    pub fn set_onboarded(&self, value: bool) -> Result<Value, String> {
        self.set_pref("onboarded", json!(value))?;
        Ok(json!({"ok": true, "onboarded": value}))
    }

    pub fn set_language(&self, language: &str) -> Result<Value, String> {
        let value = language.trim();
        let mut prefs = self.read_prefs();
        if value.is_empty() {
            prefs.remove("language");
        } else {
            prefs.insert("language".to_string(), json!(value));
        }
        self.write_prefs(&prefs)?;
        Ok(with_ok(self.settings()))
    }

    pub fn set_context_bar(&self, shown: bool) -> Result<Value, String> {
        self.set_pref("context_bar", json!(shown))?;
        Ok(json!({"ok": true, "context_bar": shown}))
    }

    pub fn set_sessions_peek(&self, count: i64) -> Result<Value, String> {
        let value = count.clamp(1, 50);
        self.set_pref("sessions_peek", json!(value))?;
        Ok(json!({"ok": true, "sessions_peek": value}))
    }

    pub fn set_scratch_base(&self, path: &str) -> Result<Value, String> {
        let value = path.trim();
        self.set_pref("scratch_base", json!(value))?;
        Ok(json!({"ok": true, "scratch_base": value}))
    }

    pub fn set_pdf_settings(&self, patch: &Value) -> Result<Value, String> {
        let mut prefs = self.read_prefs();
        if let Some(mode) = patch.get("pdf_fallback").and_then(Value::as_str) {
            if !matches!(mode, "text" | "images") {
                return Err("pdf_fallback must be 'text' or 'images'".to_string());
            }
            prefs.insert("pdf_fallback".to_string(), json!(mode));
        }
        if let Some(value) = patch.get("pdf_max_pages").and_then(Value::as_i64) {
            prefs.insert("pdf_max_pages".to_string(), json!(value.clamp(1, 100)));
        }
        if let Some(value) = patch.get("pdf_max_mb").and_then(Value::as_i64) {
            prefs.insert("pdf_max_mb".to_string(), json!(value.clamp(1, 10)));
        }
        self.write_prefs(&prefs)?;
        Ok(with_ok(self.settings()))
    }

    pub fn set_compaction_settings(&self, patch: &Value) -> Result<Value, String> {
        let mut prefs = self.read_prefs();
        if let Some(value) = patch
            .get("compaction_threshold_pct")
            .and_then(Value::as_f64)
        {
            if !(0.10..=0.95).contains(&value) {
                return Err("compaction_threshold_pct must be between 0.10 and 0.95".to_string());
            }
            prefs.insert("compaction_threshold_pct".to_string(), json!(value));
        }
        if let Some(value) = patch.get("compaction_cap_tokens").and_then(Value::as_i64) {
            prefs.insert(
                "compaction_cap_tokens".to_string(),
                json!(value.clamp(10_000, 2_000_000)),
            );
        }
        if let Some(value) = patch.get("compaction_model").and_then(Value::as_str) {
            prefs.insert("compaction_model".to_string(), json!(value));
        }
        self.write_prefs(&prefs)?;
        Ok(json!({"ok": true}))
    }

    pub fn verify_provider(&self, name: &str, fields: &Value) -> Value {
        let prefs = self.read_prefs();
        match self.probe_config(name, fields, &prefs) {
            Ok(config) => probe_provider(&config, false),
            Err(error) => json!({"ok": false, "error": error}),
        }
    }

    pub fn fetch_models(&self, name: &str, fields: &Value) -> Value {
        let mut prefs = self.read_prefs();
        let config = match self.probe_config(name, fields, &prefs) {
            Ok(config) => config,
            Err(error) => return json!({"ok": false, "error": error}),
        };
        let result = probe_provider(&config, true);
        let Some(models) = result.get("models").and_then(Value::as_array) else {
            return result;
        };
        let mut added = Vec::new();
        for model in models.iter().filter_map(Value::as_str) {
            let full = format!("{name}:{model}");
            let before = prefs
                .get("models")
                .and_then(Value::as_array)
                .is_some_and(|items| items.iter().any(|item| item.as_str() == Some(&full)));
            self.add_model_to_prefs(&mut prefs, &full);
            if !before {
                added.push(full);
            }
        }
        if let Err(error) = self.write_prefs(&prefs) {
            return json!({"ok": false, "error": error});
        }
        json!({"ok": true, "alias": name, "models": models, "added": added})
    }

    fn probe_config(
        &self,
        name: &str,
        fields: &Value,
        prefs: &Map<String, Value>,
    ) -> Result<ProbeConfig, String> {
        let descriptor = self.descriptor(name, prefs)?;
        let mut profile = self.resolved_profile(name, prefs);
        if let Some(fields) = fields.as_object() {
            for key in ["api_key", "base_url", "api_mode"] {
                if let Some(value) = fields.get(key).and_then(Value::as_str) {
                    if !value.trim().is_empty() {
                        profile.insert(key.to_string(), json!(value.trim()));
                    }
                }
            }
        }
        let base_url = profile
            .get("base_url")
            .and_then(Value::as_str)
            .unwrap_or(&descriptor.base_url)
            .trim_end_matches('/')
            .to_string();
        let api_key = self.profile_api_key(&descriptor, &profile, &base_url);
        if api_key.is_empty() {
            return Err("Enter an API key to test.".to_string());
        }
        let protocol = profile
            .get("protocol")
            .and_then(Value::as_str)
            .unwrap_or(&descriptor.protocol)
            .to_string();
        Ok(ProbeConfig {
            title: descriptor.title.to_string(),
            protocol,
            api_key,
            base_url,
        })
    }

    fn provider_row(
        &self,
        descriptor: ProviderDescriptor,
        custom: bool,
        prefs: &Map<String, Value>,
    ) -> Value {
        let profile = self.resolved_profile(&descriptor.name, prefs);
        let values: Map<String, Value> = ["base_url", "api_mode"]
            .into_iter()
            .filter_map(|key| {
                profile
                    .get(key)
                    .filter(|value| value.as_str().is_some_and(|text| !text.is_empty()))
                    .map(|value| (key.to_string(), value.clone()))
            })
            .collect();
        let fields = if descriptor.protocol == ANTHROPIC_PROTOCOL {
            anthropic_fields(&descriptor.base_url)
        } else {
            openai_fields(&descriptor.base_url, descriptor.name == "openai")
        };
        json!({
            "name": descriptor.name,
            "title": descriptor.title,
            "needs_key": true,
            "fields": fields,
            "configured": self.provider_configured(&descriptor.name, prefs),
            "values": values,
            "suggested_models": suggested_models(&descriptor.name),
            "recommended_model": descriptor.recommended_model,
            "blurb": descriptor.blurb,
            "key_set_at": profile.get("key_set_at").cloned().unwrap_or(Value::Null),
            "last_used_at": prefs.get("provider_last_used").and_then(Value::as_object).and_then(|used| used.get(&descriptor.name)).cloned().unwrap_or(Value::Null),
            "custom": custom,
            "protocol": if custom { json!(descriptor.protocol) } else { Value::Null },
            "alias": if custom { json!(descriptor.name) } else { Value::Null },
        })
    }

    fn provider_configured(&self, name: &str, prefs: &Map<String, Value>) -> bool {
        let Ok(descriptor) = self.descriptor(name, prefs) else {
            return false;
        };
        let profile = self.resolved_profile(name, prefs);
        let base_url = profile
            .get("base_url")
            .and_then(Value::as_str)
            .unwrap_or(&descriptor.base_url);
        !self
            .profile_api_key(&descriptor, &profile, base_url)
            .is_empty()
    }

    fn profile_api_key(
        &self,
        descriptor: &ProviderDescriptor,
        profile: &Map<String, Value>,
        base_url: &str,
    ) -> String {
        let stored = profile
            .get("api_key")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        if !stored.is_empty() {
            return stored.to_string();
        }
        if self.env_key_allowed(descriptor, base_url) {
            return std::env::var(&descriptor.env_key)
                .unwrap_or_default()
                .trim()
                .to_string();
        }
        String::new()
    }

    fn env_key_allowed(&self, descriptor: &ProviderDescriptor, base_url: &str) -> bool {
        matches!(descriptor.name.as_str(), "openai" | "anthropic")
            && base_url.trim_end_matches('/') == descriptor.base_url
            && !descriptor.env_key.is_empty()
    }

    fn route_model(&self, model: &str, prefs: &Map<String, Value>) -> (String, String) {
        if let Some((prefix, bare)) = model.split_once(':') {
            if self.descriptor(prefix, prefs).is_ok() {
                return (prefix.to_string(), bare.to_string());
            }
        }
        ("openai".to_string(), model.to_string())
    }

    fn descriptor(
        &self,
        name: &str,
        prefs: &Map<String, Value>,
    ) -> Result<ProviderDescriptor, String> {
        if let Some(descriptor) = self.builtin_descriptor(name) {
            return Ok(descriptor);
        }
        let protocol = prefs
            .get("provider_profiles")
            .and_then(Value::as_object)
            .and_then(|profiles| profiles.get(name))
            .and_then(Value::as_object)
            .and_then(|profile| profile.get("protocol"))
            .and_then(Value::as_str)
            .ok_or_else(|| format!("unknown provider: {name}"))?;
        validate_protocol(protocol)?;
        Ok(ProviderDescriptor {
            name: name.to_string(),
            title: name.to_string(),
            protocol: if protocol == ANTHROPIC_PROTOCOL {
                ANTHROPIC_PROTOCOL.to_string()
            } else {
                OPENAI_PROTOCOL.to_string()
            },
            base_url: if protocol == ANTHROPIC_PROTOCOL {
                DEFAULT_ANTHROPIC_URL.to_string()
            } else {
                DEFAULT_OPENAI_URL.to_string()
            },
            recommended_model: if protocol == ANTHROPIC_PROTOCOL {
                "claude-fable-5".to_string()
            } else {
                "gpt-4o".to_string()
            },
            env_key: String::new(),
            blurb: "User-defined provider".to_string(),
        })
    }

    fn builtin_descriptor(&self, name: &str) -> Option<ProviderDescriptor> {
        PROVIDERS
            .iter()
            .find(|descriptor| descriptor.name == name)
            .copied()
            .map(Into::into)
    }

    fn resolved_profile(&self, name: &str, prefs: &Map<String, Value>) -> Map<String, Value> {
        let mut secrets = self.read_secrets();
        self.migrate_legacy_profile(name, &mut secrets, prefs);
        secrets
            .get(&profile_key(name))
            .and_then(Value::as_object)
            .map(resolve_object)
            .unwrap_or_default()
    }

    fn migrate_legacy_profile(
        &self,
        name: &str,
        secrets: &mut Map<String, Value>,
        prefs: &Map<String, Value>,
    ) {
        let legacy = format!("provider:{name}");
        let target = profile_key(name);
        if !secrets.contains_key(&target) {
            if let Some(mut value) = secrets.remove(&legacy) {
                if let Some(profile) = value.as_object_mut() {
                    let protocol = self
                        .descriptor(name, prefs)
                        .map(|descriptor| descriptor.protocol)
                        .unwrap_or_else(|_| OPENAI_PROTOCOL.to_string());
                    profile
                        .entry("protocol".to_string())
                        .or_insert_with(|| json!(protocol));
                    profile
                        .entry("name".to_string())
                        .or_insert_with(|| json!(name));
                }
                secrets.insert(target, value);
                let _ = self.write_secrets(secrets);
            }
        }
    }

    fn add_model_to_prefs(&self, prefs: &mut Map<String, Value>, model: &str) {
        remove_string(prefs, "hidden_models", model);
        if !MODEL_MATRIX.iter().any(|(id, _, _)| *id == model) {
            push_unique(prefs, "models", model);
        }
    }

    fn set_pref(&self, key: &str, value: Value) -> Result<(), String> {
        let mut prefs = self.read_prefs();
        prefs.insert(key.to_string(), value);
        self.write_prefs(&prefs)
    }

    fn prefs_path(&self) -> PathBuf {
        self.state_dir.join("prefs.json")
    }

    fn secrets_path(&self) -> PathBuf {
        self.state_dir.join("secrets.json")
    }

    fn read_prefs(&self) -> Map<String, Value> {
        read_object(&self.prefs_path())
    }

    fn write_prefs(&self, prefs: &Map<String, Value>) -> Result<(), String> {
        write_object(&self.prefs_path(), prefs, false)
    }

    fn read_secrets(&self) -> Map<String, Value> {
        read_object(&self.secrets_path())
    }

    fn write_secrets(&self, secrets: &Map<String, Value>) -> Result<(), String> {
        write_object(&self.secrets_path(), secrets, true)
    }
}

struct ProbeConfig {
    title: String,
    protocol: String,
    api_key: String,
    base_url: String,
}

fn probe_provider(config: &ProbeConfig, include_models: bool) -> Value {
    let url = if config.protocol == ANTHROPIC_PROTOCOL {
        join_api_path(&config.base_url, "models")
    } else {
        format!("{}/models", config.base_url.trim_end_matches('/'))
    };
    let mut request = ureq::get(&url).timeout(std::time::Duration::from_secs(10));
    if config.protocol == ANTHROPIC_PROTOCOL {
        request = request
            .set("x-api-key", &config.api_key)
            .set("anthropic-version", "2023-06-01");
    } else if !config.api_key.is_empty() {
        request = request.set("Authorization", &format!("Bearer {}", config.api_key));
    }
    match request.call() {
        Ok(response) => {
            if !include_models {
                return json!({"ok": true});
            }
            match response.into_json::<Value>() {
                Ok(payload) => {
                    let models: Vec<&str> = payload
                        .get("data")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(|model| model.get("id").and_then(Value::as_str))
                        .collect();
                    json!({"ok": true, "models": models})
                }
                Err(_) => json!({"ok": false, "error": "Provider returned invalid model data."}),
            }
        }
        Err(ureq::Error::Status(401 | 403, _)) => {
            json!({"ok": false, "error": "Invalid API key."})
        }
        Err(ureq::Error::Status(404, _)) => json!({
            "ok": false,
            "error": "Model listing is not supported by this endpoint — add a model ID manually."
        }),
        Err(ureq::Error::Status(code, _)) => json!({
            "ok": false,
            "error": format!("{} returned HTTP {code}.", config.title)
        }),
        Err(error) => json!({
            "ok": false,
            "error": format!("Couldn't reach {} ({}).", config.title, error)
        }),
    }
}

fn openai_fields(base_url: &str, official: bool) -> Value {
    let mut fields = vec![
        json!({
            "key": "api_key", "label": if official { "OpenAI API key" } else { "API key" },
            "secret": true, "required": official, "help": "Stored and resolved only by the Rust Runtime.",
            "placeholder": "sk-…", "default": "", "choices": [], "show_when": Value::Null,
        }),
        json!({
            "key": "base_url", "label": "Endpoint", "secret": false, "required": false,
            "help": "OpenAI-compatible base URL.", "placeholder": if base_url.is_empty() { "https://…/v1" } else { base_url },
            "default": base_url, "choices": [], "show_when": Value::Null,
        }),
    ];
    if official {
        fields.push(json!({
            "key": "api_mode", "label": "API mode", "secret": false, "required": false,
            "help": "Responses is the default for OpenAI; compatible endpoints may use Chat Completions.",
            "placeholder": "", "default": "responses", "show_when": Value::Null,
            "choices": [
                {"value": "responses", "label": "Responses API"},
                {"value": "chat", "label": "Chat Completions"}
            ]
        }));
    }
    Value::Array(fields)
}

fn anthropic_fields(base_url: &str) -> Value {
    json!([
        {
            "key": "api_key", "label": "Anthropic API key", "secret": true, "required": true,
            "help": "Stored and resolved only by the Rust Runtime.", "placeholder": "sk-ant-…",
            "default": "", "choices": [], "show_when": Value::Null,
        },
        {
            "key": "base_url", "label": "Endpoint", "secret": false, "required": false,
            "help": "Anthropic-compatible base URL.", "placeholder": if base_url.is_empty() { "https://…" } else { base_url },
            "default": base_url, "choices": [], "show_when": Value::Null,
        }
    ])
}

fn suggested_models(provider: &str) -> Vec<String> {
    let mut suggestions: Vec<String> = MODEL_MATRIX
        .iter()
        .filter_map(|(model, _, _)| {
            if provider == "openai" && !model.contains(':') {
                Some((*model).to_string())
            } else {
                model
                    .strip_prefix(&format!("{provider}:"))
                    .map(str::to_string)
            }
        })
        .collect();
    let extras: &[&str] = match provider {
        "zai" => &["glm-4.6"],
        "deepseek" => &["deepseek-v4-pro"],
        "kimi" => &["kimi-k2.5"],
        "minimax" => &["MiniMax-M2.5-highspeed", "MiniMax-M3"],
        "qwen" => &["qwen3-coder-plus", "qwen-plus"],
        "xai" => &["grok-4"],
        "mistral" => &["mistral-small-latest"],
        _ => &[],
    };
    for model in extras {
        if !suggestions.iter().any(|existing| existing == model) {
            suggestions.push((*model).to_string());
        }
    }
    suggestions
}

fn validate_profile(
    descriptor: &ProviderDescriptor,
    profile: &Map<String, Value>,
    require_complete: bool,
) -> Result<(), String> {
    if require_complete
        && profile
            .get("api_key")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .is_empty()
        && std::env::var(&descriptor.env_key)
            .unwrap_or_default()
            .trim()
            .is_empty()
    {
        return Err("missing: API key".to_string());
    }
    if let Some(mode) = profile.get("api_mode").and_then(Value::as_str) {
        if !matches!(mode, "chat" | "responses") {
            return Err("api_mode must be 'chat' or 'responses'".to_string());
        }
    }
    Ok(())
}

fn validate_protocol(protocol: &str) -> Result<(), String> {
    if matches!(protocol, OPENAI_PROTOCOL | ANTHROPIC_PROTOCOL) {
        Ok(())
    } else {
        Err(format!("unknown protocol: {protocol}"))
    }
}

fn validate_alias(alias: &str) -> Result<(), String> {
    static ALIAS: OnceLock<Regex> = OnceLock::new();
    let regex = ALIAS.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,62}$").unwrap());
    if regex.is_match(alias) {
        Ok(())
    } else {
        Err("Invalid provider alias.".to_string())
    }
}

fn profile_key(name: &str) -> String {
    format!("{PROFILE_PREFIX}{name}")
}

fn resolve_object(input: &Map<String, Value>) -> Map<String, Value> {
    input
        .iter()
        .map(|(key, value)| (key.clone(), resolve_value(value)))
        .collect()
}

fn resolve_value(value: &Value) -> Value {
    match value {
        Value::String(text) => {
            static ENV_REF: OnceLock<Regex> = OnceLock::new();
            let regex =
                ENV_REF.get_or_init(|| Regex::new(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}").unwrap());
            Value::String(
                regex
                    .replace_all(text, |captures: &regex::Captures<'_>| {
                        std::env::var(&captures[1]).unwrap_or_else(|_| captures[0].to_string())
                    })
                    .into_owned(),
            )
        }
        Value::Array(values) => Value::Array(values.iter().map(resolve_value).collect()),
        Value::Object(values) => Value::Object(resolve_object(values)),
        other => other.clone(),
    }
}

fn read_object(path: &Path) -> Map<String, Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

fn write_object(path: &Path, value: &Map<String, Value>, private: bool) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let bytes = serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?;
    fs::write(path, bytes).map_err(|error| error.to_string())?;
    if private {
        restrict_secret_file(path)?;
    }
    Ok(())
}

#[cfg(unix)]
fn restrict_secret_file(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).map_err(|error| error.to_string())
}

#[cfg(windows)]
fn restrict_secret_file(_path: &Path) -> Result<(), String> {
    // Files inherit the user-only ACL applied to Delta's application-data
    // directory by the installer/first-run shell. Secret values never cross IPC.
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn restrict_secret_file(_path: &Path) -> Result<(), String> {
    Ok(())
}

fn object_mut<'a>(root: &'a mut Map<String, Value>, key: &str) -> &'a mut Map<String, Value> {
    if !root.get(key).is_some_and(Value::is_object) {
        root.insert(key.to_string(), json!({}));
    }
    root.get_mut(key).unwrap().as_object_mut().unwrap()
}

fn push_unique(root: &mut Map<String, Value>, key: &str, item: &str) {
    if !root.get(key).is_some_and(Value::is_array) {
        root.insert(key.to_string(), json!([]));
    }
    let values = root.get_mut(key).unwrap().as_array_mut().unwrap();
    if !values.iter().any(|value| value.as_str() == Some(item)) {
        values.push(json!(item));
    }
}

fn remove_string(root: &mut Map<String, Value>, key: &str, item: &str) {
    if let Some(values) = root.get_mut(key).and_then(Value::as_array_mut) {
        values.retain(|value| value.as_str() != Some(item));
    }
}

fn retain_strings_without_prefix(root: &mut Map<String, Value>, key: &str, prefix: &str) {
    if let Some(values) = root.get_mut(key).and_then(Value::as_array_mut) {
        values.retain(|value| !value.as_str().is_some_and(|text| text.starts_with(prefix)));
    }
}

fn with_ok(mut value: Value) -> Value {
    if let Some(object) = value.as_object_mut() {
        object.insert("ok".to_string(), json!(true));
    }
    value
}

fn bounded_i64(value: Option<&Value>, default: i64, min: i64, max: i64) -> i64 {
    value
        .and_then(Value::as_i64)
        .unwrap_or(default)
        .clamp(min, max)
}

fn bounded_f64(value: Option<&Value>, default: f64, min: f64, max: f64) -> f64 {
    value
        .and_then(Value::as_f64)
        .unwrap_or(default)
        .clamp(min, max)
}

fn join_api_path(base_url: &str, endpoint: &str) -> String {
    let base = base_url.trim_end_matches('/');
    if base.ends_with("/v1") {
        format!("{base}/{endpoint}")
    } else {
        format!("{base}/v1/{endpoint}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_never_expose_secret_values() {
        let temp = tempfile::tempdir().unwrap();
        let authority = ModelAuthority::new(temp.path());
        authority
            .set_provider(
                "openai",
                None,
                &json!({"api_key": "top-secret", "base_url": DEFAULT_OPENAI_URL}),
            )
            .unwrap();
        let settings = authority.settings();
        let providers = authority.providers();
        assert!(!settings.to_string().contains("top-secret"));
        assert!(!providers.to_string().contains("top-secret"));
        assert_eq!(settings["source"], "store");
        assert_eq!(settings["model_ready"], true);
    }

    #[test]
    fn model_id_resolves_provider_config_inside_rust() {
        let temp = tempfile::tempdir().unwrap();
        let authority = ModelAuthority::new(temp.path());
        authority
            .set_provider(
                "anthropic",
                None,
                &json!({"api_key": "secret", "base_url": DEFAULT_ANTHROPIC_URL}),
            )
            .unwrap();
        let config = authority
            .resolve_runtime_config("anthropic:claude-sonnet-4-6")
            .unwrap();
        assert_eq!(config.model, "claude-sonnet-4-6");
        assert_eq!(config.protocol, "anthropic");
        assert_eq!(config.base_url, DEFAULT_ANTHROPIC_URL);
        assert_eq!(config.api_key, "secret");
    }

    #[test]
    fn custom_provider_is_persisted_and_removed_with_its_models() {
        let temp = tempfile::tempdir().unwrap();
        let authority = ModelAuthority::new(temp.path());
        authority
            .set_provider(
                "office-gateway",
                Some(OPENAI_PROTOCOL),
                &json!({"api_key": "secret", "base_url": "https://example.test/v1"}),
            )
            .unwrap();
        authority.add_model("office-gateway:model-a").unwrap();
        let config = authority
            .resolve_runtime_config("office-gateway:model-a")
            .unwrap();
        assert_eq!(config.protocol, "openai_chat");
        assert_eq!(config.model, "model-a");
        authority.remove_provider("office-gateway").unwrap();
        assert!(authority
            .resolve_runtime_config("office-gateway:model-a")
            .is_err());
        assert!(!authority
            .settings()
            .to_string()
            .contains("office-gateway:model-a"));
    }

    #[test]
    fn legacy_provider_profile_migrates_on_read() {
        let temp = tempfile::tempdir().unwrap();
        fs::write(
            temp.path().join("secrets.json"),
            r#"{"provider:openai":{"api_key":"legacy"}}"#,
        )
        .unwrap();
        let authority = ModelAuthority::new(temp.path());
        let config = authority.resolve_runtime_config("gpt-5.5").unwrap();
        assert_eq!(config.api_key, "legacy");
        let secrets = fs::read_to_string(temp.path().join("secrets.json")).unwrap();
        assert!(secrets.contains("provider-profile:openai"));
        assert!(!secrets.contains("provider:openai"));
    }
}
