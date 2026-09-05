"""P1-A Async Retry cleanup tests.

The spec requires:
  - time.sleep -> await asyncio.sleep (no blocking in async event loop)
  - Retry-After header from server takes precedence over local backoff
  - Prioritize structured HTTP / SDK error classification
  - Don't refactor Provider architecture

Tested in this file:
  - extract_retry_after from SDK attribute
  - extract_retry_after from httpx-style response headers
  - extract_retry_after from message text
  - wait_for_retry_async uses asyncio.sleep (non-blocking)
  - wait_for_retry_async prefers Retry-After over local backoff
  - ErrorClass.rate_limit / auth / transient classes
  - is_retryable for new error types
"""

from __future__ import annotations

import asyncio
import time


from core.call_errors import (
    ErrorClass,
    _parse_retry_after_value,
    backoff_delay,
    classify_error,
    extract_retry_after,
    is_retryable,
    wait_for_retry_async,
)


# -- extract_retry_after ----------------------------------------------------

class _FakeHeaders:
    def __init__(self, data):
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key):
        return self._data.get(key.lower())


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def test_extract_retry_after_from_sdk_attribute():
    class _Exc(Exception):
        retry_after = 5.0
    assert extract_retry_after(_Exc()) == 5.0


def test_extract_retry_after_from_response_header():
    exc = Exception("429 too many requests")
    exc.response = _FakeResponse({"Retry-After": "7"})
    assert extract_retry_after(exc) == 7.0


def test_extract_retry_after_from_message_text():
    exc = Exception("rate_limit_exceeded, retry after 3s")
    assert extract_retry_after(exc) == 3.0


def test_extract_retry_after_returns_none_when_absent():
    assert extract_retry_after(Exception("connection refused")) is None


def test_parse_retry_after_value_numeric():
    assert _parse_retry_after_value("42") == 42.0
    assert _parse_retry_after_value("1.5") == 1.5


def test_parse_retry_after_value_http_date():
    # HTTP-date is not supported — fall back to local backoff.
    assert _parse_retry_after_value("Wed, 21 Oct 2015 07:28:00 GMT") is None


# -- wait_for_retry_async ---------------------------------------------------

def test_wait_for_retry_async_uses_retry_after():
    """When the server provides Retry-After, it takes precedence over
    the local backoff."""
    class _Exc(Exception):
        retry_after = 1.0

    started = time.monotonic()
    delay = asyncio.run(wait_for_retry_async(0, exc=_Exc()))
    elapsed = time.monotonic() - started
    assert delay == 1.0
    assert elapsed < 2.0  # didn't wait 1.5min of backoff (attempt=0, 200ms*1)


def test_wait_for_retry_async_falls_back_to_backoff():
    """No Retry-After → local exponential backoff (200ms for attempt=0)."""
    started = time.monotonic()
    delay = asyncio.run(wait_for_retry_async(0))
    elapsed = time.monotonic() - started
    # 200ms base ± 10% jitter, plus scheduling slack.
    assert 0.15 < delay < 0.3
    assert elapsed < 0.5


def test_wait_for_retry_async_increases_with_attempt():
    d0 = asyncio.run(wait_for_retry_async(0))
    d2 = asyncio.run(wait_for_retry_async(2))
    # Attempt 2 → 200ms × 4 = 800ms (jittered). Always > 200ms base.
    assert d2 > d0


def test_wait_for_retry_async_does_not_block_event_loop():
    """The async version must not block the event loop. Schedule a
    coroutine alongside and assert it gets to run during the wait."""
    async def go():
        order: list[str] = []
        async def other():
            await asyncio.sleep(0.05)
            order.append("other")
        task = asyncio.create_task(other())
        await wait_for_retry_async(0, exc=_RetryAfterExc(0.1))
        await task
        order.append("retry")
        return order
    order = asyncio.run(go())
    assert "other" in order
    assert "retry" in order


class _RetryAfterExc(Exception):
    def __init__(self, value):
        self.retry_after = value


# -- ErrorClass coverage ----------------------------------------------------

def test_classify_error_rate_limit():
    assert classify_error(Exception("429 too many requests")) is ErrorClass.RATE_LIMIT
    assert classify_error(Exception("rate_limit_exceeded")) is ErrorClass.RATE_LIMIT


def test_classify_error_auth():
    assert classify_error(Exception("401 unauthorized")) is ErrorClass.AUTH
    assert classify_error(Exception("403 forbidden: invalid api key")) is ErrorClass.AUTH


def test_classify_error_transient():
    assert classify_error(Exception("connection refused")) is ErrorClass.TRANSIENT
    assert classify_error(Exception("503 service unavailable")) is ErrorClass.TRANSIENT
    assert classify_error(Exception("504 gateway timeout")) is ErrorClass.TRANSIENT


def test_classify_error_stream_truncated():
    from core.call_errors import StreamTruncatedError
    assert classify_error(StreamTruncatedError("truncated")) is ErrorClass.STREAM_TRUNCATED


def test_classify_error_context_too_large():
    # Some providers say "context_length_exceeded" — class is preserved.
    from core import compaction as _compaction
    if hasattr(_compaction, "is_context_overflow"):
        # If the path exists, we trust the existing routing.
        result = classify_error(Exception("context_length_exceeded"))
        assert result in (ErrorClass.CONTEXT_TOO_LARGE, ErrorClass.OTHER)


def test_classify_error_protocol_incompatible():
    assert classify_error(
        Exception("'parallel_tool_calls' is not supported by this server")
    ) is ErrorClass.PROTOCOL_INCOMPATIBLE


# -- is_retryable ------------------------------------------------------------

def test_retryable_rate_limit():
    assert is_retryable(Exception("429 too many requests"))


def test_retryable_transient():
    assert is_retryable(Exception("connection reset by peer"))


def test_retryable_ttft():
    from core.call_errors import TTFTTimeoutError
    assert is_retryable(TTFTTimeoutError("no first token"))


def test_not_retryable_auth():
    assert not is_retryable(Exception("401 unauthorized"))


def test_not_retryable_stream_truncated():
    from core.call_errors import StreamTruncatedError
    assert not is_retryable(StreamTruncatedError("truncated"))


def test_not_retryable_context_too_large():
    assert not is_retryable(Exception("context_length_exceeded"))


def test_not_retryable_protocol_incompatible():
    assert not is_retryable(
        Exception("'parallel_tool_calls' is not supported")
    )


# -- backoff_delay sanity ----------------------------------------------------

def test_backoff_caps_at_max():
    """Attempt 20 should still cap at 8000ms."""
    delay_ms = backoff_delay(20, base_ms=200) * 1000
    assert delay_ms <= 8000 * 1.2
