"""Request observability (v0.3.0 P0): one JSONL row per model call.

Before this, a slow/timeout model call was invisible: nothing recorded how big the
prompt was, how many tool schemas rode along, or how long the upstream took to first
token — so "Delta times out where OpenCode answers" couldn't be distinguished from a
network problem. The turn engine now records one observation per call:

    ts, provider, model, messages_count, body_bytes, tools_count, tool_mode,
    tool_names, context_estimate_tokens, ttft_ms, duration_ms, outcome,
    error_type, context_tokens

`body_bytes` is the serialized prompt (messages + tools) — the number that actually
drives prompt processing on weak gateways. `ttft_ms` is time-to-first-chunk (the
timeout killer on free nodes). `outcome` distinguishes ok / error / interrupted;
`error_type` carries the exception class. Rows go to `<state_dir>/request_log.jsonl`,
best-effort: a logging failure must never break a model call.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

# Rotate at 5 MB, keeping one generation — enough history to compare providers and
# models without growing unbounded.
_ROTATE_BYTES = 5_000_000

_lock = threading.Lock()
_default: Callable[[dict[str, Any]], None] | None = None


def make_logger(path: Path) -> Callable[[dict[str, Any]], None]:
    """A JSONL sink for request observations. Thread-safe (multiple sessions share the
    file) and best-effort by contract — callers rely on us never raising."""

    def log(record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str)
        with _lock:
            try:
                if path.exists() and path.stat().st_size >= _ROTATE_BYTES:
                    path.replace(path.with_suffix(".jsonl.1"))
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass  # observability must never break the turn

    return log


def default_logger() -> Callable[[dict[str, Any]], None]:
    """The process-wide sink at `<state_dir>/request_log.jsonl` (created lazily)."""
    global _default
    if _default is None:
        from packages.secrets import state_dir

        _default = make_logger(state_dir() / "request_log.jsonl")
    return _default
