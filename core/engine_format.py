"""Pure message-formatting helpers for the engine's persisted message list.

Split out of ``engine.py``. These four functions are stateless: they turn in-memory turn/tool
objects into the JSON-shaped message dicts that the engine persists and replays, with no
dependency on the ``TurnEngine`` instance or its mutable state. Keeping them here separates
"shape a message" from "run the turn loop".
"""

from __future__ import annotations

import json
import time
from typing import Any

from providers import AssistantTurn, ToolCall


def _assistant_message(turn: AssistantTurn, model: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": turn.text or "",
        "ts": time.time(),
    }
    if turn.usage is not None:
        # Display/aggregation sidecar (like `reasoning`): persisted with the message,
        # stripped before provider calls. Tagged with the model that produced it so
        # per-model rollups survive mid-session model switches.
        message["usage"] = {"model": model, **turn.usage.as_dict()}
    if turn.reasoning:
        # Display-only thinking text — rendered by the GUI, stripped for every provider
        # (`_outbound_messages`); provider-private replay blocks go via `extras` instead.
        message["reasoning"] = turn.reasoning
    if turn.extras:
        # Provider-private sidecars (e.g. `_gemini` thought signatures) persist with the
        # message; the owning provider reattaches them, the rest strip them (base.py).
        message.update(turn.extras)
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ]
    return message


def _tool_result_message(tool_call: ToolCall, result: Any) -> dict[str, Any]:
    content = result if isinstance(result, str) else json.dumps(result, default=str)
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": content,
        "ts": time.time(),
    }


def _tool_error_message(tool_call: ToolCall, reason: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps({"error": "tool call not executed", "reason": reason}),
        "ts": time.time(),
    }


def _preview(value: Any, max_chars: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", "\\n")
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."
