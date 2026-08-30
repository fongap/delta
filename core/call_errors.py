"""Model-call error classification (v0.3.0 P1).

The request log used to carry only the exception class name as `error_type` — which
couldn't tell a "gateway killed the stream before completion" from a "this server
doesn't speak the dialect we assumed". This module classifies a provider failure into
the four buckets that matter for diagnosis and retry policy:

- `ttft_timeout`        — no first token within the TTFT ceiling (free/shared node stall)
- `stream_truncated`    — the stream ended WITHOUT a finish_reason (relay/gateway kill)
- `protocol_incompatible` — the server rejected a parameter/shape we assumed standard
- `context_too_large`   — prompt exceeds the model window (routes into compaction)
- `other`               — everything else (kept, so a class is always available)

It also owns the bounded retry policy (Codex-absorbed): transient/transport failures
(429, connection, TTFT-stall) earn a bounded exponential backoff (200ms base, ×2,
±10% jitter); `stream_truncated` and `context_too_large` are NOT auto-retried — the
former must keep failing loudly (finish_reason guard), the latter belongs to compaction.
"""

from __future__ import annotations

import random
import time
from enum import Enum
from typing import Any

# -- error taxonomy -----------------------------------------------------------

_ERROR_CLASS = (
    "ttft_timeout",
    "stream_truncated",
    "protocol_incompatible",
    "context_too_large",
    "other",
)


class ErrorClass(str, Enum):
    TTFT_TIMEOUT = "ttft_timeout"
    STREAM_TRUNCATED = "stream_truncated"
    PROTOCOL_INCOMPATIBLE = "protocol_incompatible"
    CONTEXT_TOO_LARGE = "context_too_large"
    OTHER = "other"


class StreamTruncatedError(RuntimeError):
    """The upstream closed the stream without a finish_reason (the 0.2.2 guard). Never
    auto-retried: presenting a partial fragment as the answer is the exact failure the
    guard exists to prevent."""


class TTFTTimeoutError(TimeoutError):
    """No first token within the TTFT ceiling — a stalled/wedged upstream."""


class ProtocolIncompatibleError(RuntimeError):
    """The server rejected a parameter/shape we assumed a compliant endpoint accepts.
    Retrying with the same body would fail identically; surfaced for the user (and, when
    a param is nameable, remembered in the endpoint profile so later calls skip it)."""


# -- classification -----------------------------------------------------------

# Protocol-incompatible markers in the error body — a server that says any of these
# about a param we sent speaks a dialect we mis-assumed (NOT a transport blip).
_PROTOCOL_MARKERS = (
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
)


def classify_error(exc: BaseException) -> ErrorClass:
    """The failure bucket for a provider exception, used for the request log and the
    retry policy. Order matters: specific markers beat the generic fallbacks."""
    if isinstance(exc, StreamTruncatedError):
        return ErrorClass.STREAM_TRUNCATED
    if isinstance(exc, TTFTTimeoutError):
        return ErrorClass.TTFT_TIMEOUT
    if isinstance(exc, ProtocolIncompatibleError):
        return ErrorClass.PROTOCOL_INCOMPATIBLE
    # Context overflow routes into the compaction policy (and is never retried here).
    from core import compaction as _compaction

    if _compaction.is_context_overflow(exc):
        return ErrorClass.CONTEXT_TOO_LARGE
    text = str(exc).lower()
    if any(marker in text for marker in _PROTOCOL_MARKERS):
        return ErrorClass.PROTOCOL_INCOMPATIBLE
    # The stream-truncation guard's message (kept when the dedicated type isn't used).
    if "finish_reason" in text and ("truncat" in text or "上游流式响应被截断" in text):
        return ErrorClass.STREAM_TRUNCATED
    return ErrorClass.OTHER


def is_retryable(exc: BaseException) -> bool:
    """Whether a failure earns the bounded exponential backoff. Transport/transient
    only — never stream_truncated (the finish_reason guard stays loud) and never
    context_too_large (that belongs to the compaction policy)."""
    cls = classify_error(exc)
    if cls in (ErrorClass.STREAM_TRUNCATED, ErrorClass.CONTEXT_TOO_LARGE):
        return False
    if cls is ErrorClass.TTFT_TIMEOUT:
        # A pre-first-token stall is exactly the free/shared-node failure to retry:
        # nothing was delivered, so re-issuing the call is safe.
        return True
    text = str(exc).lower()
    # 429/5xx/connection/timeout markers.
    return any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "too many requests",
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
        )
    )


# -- bounded exponential backoff (Codex-absorbed) -----------------------------

_BACKOFF_BASE_MS = 200
_BACKOFF_FACTOR = 2.0
_BACKOFF_MAX_MS = 8_000
_BACKOFF_JITTER = 0.1


def backoff_delay(attempt: int, *, base_ms: float = _BACKOFF_BASE_MS) -> float:
    """Delay (seconds) for retry `attempt` (0-based): base × factor^attempt, jittered
    ±10%, capped. Matches Codex's `util::backoff` shape (200ms, ×2, ±10%)."""
    exp = _BACKOFF_FACTOR ** attempt
    raw = base_ms * exp
    raw = min(raw, _BACKOFF_MAX_MS)
    jitter = 1.0 + random.uniform(-_BACKOFF_JITTER, _BACKOFF_JITTER)
    return (raw * jitter) / 1000.0


def wait_for_retry(attempt: int, *, base_ms: float = _BACKOFF_BASE_MS) -> float:
    """Blocking wait for retry `attempt`; returns the delay actually slept (seconds).
    Tests monkeypatch `time.sleep`/`random` to make retries instant/deterministic."""
    delay = backoff_delay(attempt, base_ms=base_ms)
    time.sleep(delay)
    return delay
