//! Retry Policy Decision Authority (ADR-039).
//!
//! The retry policy **decision** — whether a provider failure is retryable
//! — is Rust-authoritative. Python retains the retry execution mechanism
//! (backoff delay math, async sleep, Retry-After header extraction, retry
//! loop, budget tracking).
//!
//! The classification logic mirrors Python's `core/call_errors.py`
//! `classify_error()`, but operates on the error TYPE NAME and MESSAGE
//! TEXT rather than Python isinstance checks. Python passes:
//!
//! - `error_type`: the exception class name (e.g. "TTFTTimeoutError")
//! - `error_message`: `str(exc)`
//! - `is_context_overflow`: whether `compaction.is_context_overflow(exc)`
//!   returned True (Python checks this; Rust trusts the flag)
//!
//! Rust returns the error class and retryability.

use serde::{Deserialize, Serialize};

/// Input for a retry classification decision.
#[derive(Debug, Clone, Deserialize)]
pub struct RetryClassifyInput {
    /// Python exception class name (e.g. "TTFTTimeoutError",
    /// "StreamTruncatedError", "httpx.ConnectError").
    pub error_type: String,
    /// `str(exc)` — the exception message text used for classification.
    pub error_message: String,
    /// Whether Python's compaction module identified this as a context
    /// overflow. Rust trusts the flag; it does not re-check.
    #[serde(default)]
    pub is_context_overflow: bool,
}

/// The error class buckets, matching Python's `ErrorClass` enum.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ErrorClass {
    RateLimit,
    TtftTimeout,
    Transient,
    StreamTruncated,
    ProtocolIncompatible,
    ContextTooLarge,
    Auth,
    Other,
}

impl ErrorClass {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::RateLimit => "rate_limit",
            Self::TtftTimeout => "ttft_timeout",
            Self::Transient => "transient",
            Self::StreamTruncated => "stream_truncated",
            Self::ProtocolIncompatible => "protocol_incompatible",
            Self::ContextTooLarge => "context_too_large",
            Self::Auth => "auth",
            Self::Other => "other",
        }
    }

    pub fn is_retryable(&self) -> bool {
        matches!(self, Self::RateLimit | Self::TtftTimeout | Self::Transient)
    }
}

/// Output of a `retry.classify` decision.
#[derive(Debug, Clone, Serialize)]
pub struct RetryClassifyOutput {
    pub error_class: String,
    pub retryable: bool,
}

// Protocol-incompatible markers in the error body — a server that says any of these
// about a param we sent speaks a dialect we mis-assumed (NOT a transport blip).
const PROTOCOL_MARKERS: &[&str] = &[
    "is not supported",
    "not supported",
    "unknown parameter",
    "unexpected parameter",
    "unexpected field",
    "does not support",
    "invalid parameter",
    "unsupported parameter",
    "not recognized",
    "'stream_options'",
    "'max_tokens'",
    "'parallel_tool_calls'",
    "'reasoning_effort'",
];

/// Classify a provider failure into an error bucket and decide retryability.
///
/// Authority rule (ADR-039):
/// - Known Python exception types (by class name) → direct classification.
/// - Context overflow flag → `context_too_large` (not retryable).
/// - Text matching: auth markers, rate-limit markers, protocol markers,
///   stream truncation, transient markers.
/// - Fallback: `other`.
///
/// Retryable: `rate_limit`, `ttft_timeout`, `transient`.
/// Not retryable: everything else.
pub fn classify_error(input: &RetryClassifyInput) -> RetryClassifyOutput {
    let class = classify_impl(input);
    RetryClassifyOutput {
        error_class: class.as_str().to_string(),
        retryable: class.is_retryable(),
    }
}

fn classify_impl(input: &RetryClassifyInput) -> ErrorClass {
    // Known Python exception types — direct classification by class name.
    let et = input.error_type.as_str();
    if et == "StreamTruncatedError" {
        return ErrorClass::StreamTruncated;
    }
    if et == "TTFTTimeoutError" {
        return ErrorClass::TtftTimeout;
    }
    if et == "ProtocolIncompatibleError" {
        return ErrorClass::ProtocolIncompatible;
    }

    // Context overflow — Python checked this; Rust trusts the flag.
    if input.is_context_overflow {
        return ErrorClass::ContextTooLarge;
    }

    let text = input.error_message.to_lowercase();

    // Auth markers (401/403) — never retried. But 429 is rate-limit, not auth.
    if any_contains(
        &text,
        &[
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "authentication",
        ],
    ) && !text.contains("429")
    {
        return ErrorClass::Auth;
    }

    // Rate-limit markers — retried, respects Retry-After.
    if any_contains(
        &text,
        &["429", "rate limit", "too many requests", "rate_limit"],
    ) {
        return ErrorClass::RateLimit;
    }

    // Protocol-incompatible markers — NOT retried.
    if any_contains(&text, PROTOCOL_MARKERS) {
        return ErrorClass::ProtocolIncompatible;
    }

    // Stream truncation markers — NOT retried.
    if text.contains("finish_reason")
        && (text.contains("truncat") || text.contains("上游流式响应被截断"))
    {
        return ErrorClass::StreamTruncated;
    }

    // Transient: 5xx / connection / timeout / service unavailable.
    if any_contains(
        &text,
        &[
            "timeout",
            "timed out",
            "connection",
            "connection refused",
            "connection reset",
            "temporarily unavailable",
            "service unavailable",
            "internal server error",
            "502",
            "503",
            "504",
        ],
    ) {
        return ErrorClass::Transient;
    }

    ErrorClass::Other
}

fn any_contains(text: &str, markers: &[&str]) -> bool {
    markers.iter().any(|m| text.contains(m))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(error_type: &str, message: &str) -> RetryClassifyInput {
        RetryClassifyInput {
            error_type: error_type.to_string(),
            error_message: message.to_string(),
            is_context_overflow: false,
        }
    }

    #[test]
    fn ttft_timeout_is_retryable() {
        let out = classify_error(&input("TTFTTimeoutError", "No first token in 90s"));
        assert_eq!(out.error_class, "ttft_timeout");
        assert!(out.retryable);
    }

    #[test]
    fn stream_truncated_not_retryable() {
        let out = classify_error(&input(
            "StreamTruncatedError",
            "stream ended without finish_reason",
        ));
        assert_eq!(out.error_class, "stream_truncated");
        assert!(!out.retryable);
    }

    #[test]
    fn protocol_incompatible_not_retryable() {
        let out = classify_error(&input("ProtocolIncompatibleError", "unsupported parameter"));
        assert_eq!(out.error_class, "protocol_incompatible");
        assert!(!out.retryable);
    }

    #[test]
    fn context_overflow_not_retryable() {
        let inp = RetryClassifyInput {
            error_type: "httpx.HTTPStatusError".to_string(),
            error_message: "400 Bad Request context_length_exceeded".to_string(),
            is_context_overflow: true,
        };
        let out = classify_error(&inp);
        assert_eq!(out.error_class, "context_too_large");
        assert!(!out.retryable);
    }

    #[test]
    fn rate_limit_is_retryable() {
        let out = classify_error(&input("httpx.HTTPStatusError", "429 Too Many Requests"));
        assert_eq!(out.error_class, "rate_limit");
        assert!(out.retryable);
    }

    #[test]
    fn auth_not_retryable() {
        let out = classify_error(&input("httpx.HTTPStatusError", "401 Unauthorized"));
        assert_eq!(out.error_class, "auth");
        assert!(!out.retryable);
    }

    #[test]
    fn transient_5xx_is_retryable() {
        let out = classify_error(&input("httpx.HTTPStatusError", "503 Service Unavailable"));
        assert_eq!(out.error_class, "transient");
        assert!(out.retryable);
    }

    #[test]
    fn connection_error_is_retryable() {
        let out = classify_error(&input("httpx.ConnectError", "connection refused"));
        assert_eq!(out.error_class, "transient");
        assert!(out.retryable);
    }

    #[test]
    fn generic_error_is_other() {
        let out = classify_error(&input("ValueError", "something weird happened"));
        assert_eq!(out.error_class, "other");
        assert!(!out.retryable);
    }

    #[test]
    fn rate_limit_takes_precedence_over_auth_markers() {
        // 429 contains "4" and "2" but should be rate_limit, not auth.
        let out = classify_error(&input("httpx.HTTPStatusError", "429 rate limit exceeded"));
        assert_eq!(out.error_class, "rate_limit");
        assert!(out.retryable);
    }
}
