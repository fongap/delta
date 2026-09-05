"""Model-call error classification (v0.3.0 P1, P1-A Async Retry).

The request log used to carry only the exception class name as `error_type` — which
couldn't tell a "gateway killed the stream before completion" from a "this server
doesn't speak the dialect we assumed". This module classifies a provider failure into
the buckets that matter for diagnosis and retry policy:

- `rate_limit`         — 429 / too many requests (retryable; respects Retry-After)
- `ttft_timeout`        — no first token within the TTFT ceiling (retryable)
- `transient`           — 5xx / connection / service unavailable (retryable)
- `stream_truncated`    — the stream ended WITHOUT a finish_reason (NOT retried)
- `context_too_large`   — prompt exceeds the model window (routes into compaction)
- `protocol_incompatible` — the server rejected a parameter/shape (NOT retried)
- `auth`                — 401 / 403 / invalid key (NOT retried)
- `other`               — everything else (kept, so a class is always available)

Retry policy (Codex-absorbed): retryable failures earn a bounded exponential
backoff (200ms base, x2, +/-10% jitter, 8s cap). When the server provides a
`Retry-After` header, it takes precedence over the local backoff.
"""

from __future__ import annotations

import random
import time
from enum import Enum

# -- error taxonomy -----------------------------------------------------------

_ERROR_CLASS = (
    "rate_limit",
    "ttft_timeout",
    "transient",
    "stream_truncated",
    "protocol_incompatible",
    "context_too_large",
    "auth",
    "other",
)


class ErrorClass(str, Enum):
    RATE_LIMIT = "rate_limit"
    TTFT_TIMEOUT = "ttft_timeout"
    TRANSIENT = "transient"
    STREAM_TRUNCATED = "stream_truncated"
    PROTOCOL_INCOMPATIBLE = "protocol_incompatible"
    CONTEXT_TOO_LARGE = "context_too_large"
    AUTH = "auth"
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
    from core import compaction as _compaction

    if _compaction.is_context_overflow(exc):
        return ErrorClass.CONTEXT_TOO_LARGE
    text = str(exc).lower()
    # Auth markers (401/403) — never retried.
    if any(m in text for m in ("401", "403", "unauthorized", "forbidden", "invalid api key", "authentication")):
        if "429" not in text:
            return ErrorClass.AUTH
    # Rate-limit markers — retried, respects Retry-After.
    if any(m in text for m in ("429", "rate limit", "too many requests", "rate_limit")):
        return ErrorClass.RATE_LIMIT
    if any(marker in text for marker in _PROTOCOL_MARKERS):
        return ErrorClass.PROTOCOL_INCOMPATIBLE
    if "finish_reason" in text and ("truncat" in text or "上游流式响应被截断" in text):
        return ErrorClass.STREAM_TRUNCATED
    # Transient: 5xx / connection / timeout / service unavailable.
    if any(
        marker in text
        for marker in (
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
    ):
        return ErrorClass.TRANSIENT
    return ErrorClass.OTHER


def is_retryable(exc: BaseException) -> bool:
    """Whether a failure earns the bounded exponential backoff. Rate-limit,
    TTFT-timeout, and transient failures are retryable; stream_truncated,
    context_too_large, protocol_incompatible, and auth are NOT."""
    cls = classify_error(exc)
    return cls in (
        ErrorClass.RATE_LIMIT,
        ErrorClass.TTFT_TIMEOUT,
        ErrorClass.TRANSIENT,
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


def extract_retry_after(exc: BaseException) -> float | None:
    """Extract a server-provided Retry-After delay (seconds) from a provider
    exception. Checks:
    1. An explicit ``retry_after`` attribute (SDK convention)
    2. The exception message for ``retry after Ns`` / ``Retry-After: N``
    3. The ``response`` attribute's headers (httpx SDK pattern)

    Returns None when no server hint is present; the caller falls back to
    the local exponential backoff.
    """
    # SDK convention: some SDK exceptions carry a `retry_after` attribute.
    ra = getattr(exc, "retry_after", None)
    if isinstance(ra, (int, float)) and ra > 0:
        return float(ra)
    # httpx SDK pattern: response.headers
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            raw = headers.get("retry-after") or headers.get("Retry-After")
            if raw:
                parsed = _parse_retry_after_value(str(raw))
                if parsed is not None:
                    return parsed
    # Text markers: "retry after 2s", "Retry-After: 5", etc.
    text = str(exc)
    import re

    match = re.search(r"retry[-_ ]?after[:\s]+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _parse_retry_after_value(raw: str) -> float | None:
    """Parse a Retry-After header value (seconds or HTTP-date). Only seconds
    are supported; HTTP-date returns None (fall back to local backoff)."""
    try:
        return abs(float(raw))
    except ValueError:
        return None


async def wait_for_retry_async(
    attempt: int,
    *,
    base_ms: float = _BACKOFF_BASE_MS,
    exc: BaseException | None = None,
) -> float:
    """Async-safe retry wait. Prioritizes server-provided Retry-After over
    the local exponential backoff. Returns the delay actually awaited."""
    if exc is not None:
        server_delay = extract_retry_after(exc)
        if server_delay is not None:
            import asyncio

            await asyncio.sleep(server_delay)
            return server_delay
    delay = backoff_delay(attempt, base_ms=base_ms)
    import asyncio

    await asyncio.sleep(delay)
    return delay
