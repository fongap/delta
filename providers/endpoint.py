"""Endpoint capability profile (v0.3.0 P0): stop assuming every OpenAI-compatible
endpoint speaks identical Chat Completions.

Today the OpenAI provider probes params reactively — send `stream_options`, eat the
400, retry without it — and forgets the answer before the next call, so every turn pays
the same failed round trip. This module gives each endpoint a small capability profile:

    ai-gateway:
      stream_options = true/false        (usage opt-in; old servers 400 on it)
      reasoning_content = true/false     (thinking deltas in the stream)
      parallel_tool_calls = true/false
      max_context = <tokens or None>

Sources, strongest first: (1) the provider profile's explicit fields (Settings ▸ Models,
keys as above) — the user's word is final; (2) LEARNED facts — when a server rejects a
param mid-call, the provider records it here keyed by endpoint so later calls skip it
proactively; (3) documented defaults. The reactive retry stays as the safety net either
way — the profile only removes the predictable 400, it doesn't replace the fix.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# Defaults describe the compliant modern surface (the common case). A param that's in
# the profile with a False value is never sent; the reactive retry covers the rest.
DEFAULTS = {
    "stream_options": True,
    "reasoning_content": True,
    "parallel_tool_calls": True,
}
# Profile fields that may be explicitly set (int for max_context, bool for the rest).
_KNOWN = (*DEFAULTS, "max_context")

_lock = threading.Lock()


@dataclass(frozen=True)
class EndpointCaps:
    """What one endpoint (keyed by base URL) is known to accept.

    `declared` lists the fields the provider profile EXPLICITLY set — a declared
    `stream_options=true` must beat a learned `false` (the user's word is final), while
    an undeclared field keeps the default, which is indistinguishable from unset.
    """

    stream_options: bool = True
    reasoning_content: bool = True
    parallel_tool_calls: bool = True
    max_context: int | None = None
    declared: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream_options": self.stream_options,
            "reasoning_content": self.reasoning_content,
            "parallel_tool_calls": self.parallel_tool_calls,
            "max_context": self.max_context,
        }


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return None


def from_profile(profile: dict[str, Any] | None) -> EndpointCaps:
    """Endpoint caps declared explicitly in the provider profile (Settings ▸ Models).
    Unknown/invalid fields are ignored; absent fields keep the defaults (the caller
    merges learned facts underneath)."""
    profile = profile or {}
    updates: dict[str, Any] = {}
    for key in DEFAULTS:
        value = _coerce_bool(profile.get(key))
        if value is not None:
            updates[key] = value
    try:
        max_context = int(str(profile.get("max_context") or "").strip())
    except ValueError:
        max_context = None
    if max_context and max_context > 0:
        updates["max_context"] = max_context
    caps = {**EndpointCaps().as_dict(), **updates}
    return EndpointCaps(**caps, declared=frozenset(updates))


def _store_path() -> Path:
    from packages.secrets import state_dir

    return state_dir() / "endpoint_caps.json"


def _learned() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def learned_caps(endpoint_key: str | None) -> dict[str, Any]:
    """Facts learned from earlier rejections for this endpoint (best-effort read)."""
    if not endpoint_key:
        return {}
    return _learned().get(endpoint_key, {})


def record_rejection(endpoint_key: str | None, field: str) -> None:
    """The server rejected `field` mid-call: remember it so future calls never send it.
    Explicit profile settings still win at merge time — a user override that turns out
    wrong keeps costing the reactive retry, which is the honest outcome."""
    if not endpoint_key or field not in DEFAULTS:
        return
    with _lock:
        try:
            store = _learned()
            entry = store.get(endpoint_key, {})
            entry[field] = False
            entry["updated_at"] = time.time()
            store[endpoint_key] = entry
            path = _store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(store), encoding="utf-8")
        except Exception:
            pass  # learning is best-effort; the reactive retry still handled the call


def merge(learned: dict[str, Any], explicit: EndpointCaps | None) -> EndpointCaps:
    """Effective caps: explicit profile fields override learned facts override defaults.
    Only fields the profile DECLARED override learning — an untouched default must not
    silently re-enable a param the server already rejected once."""
    caps = EndpointCaps()
    if learned:
        merged = {**caps.as_dict(), **{
            k: v for k, v in learned.items() if k in _KNOWN
        }}
        caps = EndpointCaps(**{
            k: (bool(v) if k in DEFAULTS else v) for k, v in merged.items()
        })
    if explicit is None:
        return caps
    overrides = {k: getattr(explicit, k) for k in explicit.declared}
    return replace(caps, **overrides) if overrides else caps
