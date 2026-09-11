"""Named inbox routing, delivery bindings, and inbound reply correlation."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from packages.jsonstate import load_json_state, save_json_state

DEFAULT_INBOX = "default"
_ID_TOKEN = re.compile(r"\[d:([0-9a-f]{6,})\]")


@dataclass
class InboxBinding:
    name: str
    channel: str | None = None  # None (in-app only) | "slack" | "telegram"
    target: str = ""  # channel id / chat id for the binding


class InboxRouting:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._bindings: dict[str, InboxBinding] = {
            DEFAULT_INBOX: InboxBinding(DEFAULT_INBOX)
        }
        self._persona_default: dict[str, str] = {}
        self._session_override: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path and self.path.is_file():
            data = load_json_state(self.path, {}) or {}
            for raw in data.get("bindings", []):
                b = InboxBinding(**raw)
                self._bindings[b.name] = b
            self._persona_default = dict(data.get("persona_default", {}))
            self._session_override = dict(data.get("session_override", {}))

    def _save(self) -> None:
        if not self.path:
            return
        save_json_state(
            self.path,
            {
                "bindings": [asdict(b) for b in self._bindings.values()],
                "persona_default": self._persona_default,
                "session_override": self._session_override,
            },
        )

    def set_binding(
        self, name: str, *, channel: str | None = None, target: str = ""
    ) -> None:
        with self._lock:
            self._bindings[name] = InboxBinding(name, channel, target)
            self._save()

    def binding_for(self, name: str) -> InboxBinding:
        return self._bindings.get(name) or InboxBinding(name)

    def set_persona_default(self, persona_id: str, inbox_name: str) -> None:
        with self._lock:
            self._persona_default[persona_id] = inbox_name
            self._save()

    def set_session_override(self, session_id: str, inbox_name: str) -> None:
        with self._lock:
            self._session_override[session_id] = inbox_name
            self._save()

    def route_for(self, session_id: str, persona_id: str | None = None) -> str:
        """Per-session override > persona default > global default."""
        if session_id in self._session_override:
            return self._session_override[session_id]
        if persona_id and persona_id in self._persona_default:
            return self._persona_default[persona_id]
        return DEFAULT_INBOX

    def bindings(self) -> list[dict]:
        return [asdict(b) for b in self._bindings.values()]


Sender = Callable[[str, str, str], None]


def deliver(item, binding: InboxBinding, sender: Sender | None) -> bool:
    """Mirror an inbox item to its bound channel and embed its correlation id."""
    if not binding.channel or sender is None:
        return False
    text = f"{item.title}\n{item.body}\n[d:{item.id}]".strip()
    sender(binding.channel, binding.target, text)
    return True


# Only the leading token carries approval intent. This avoids accidental matches
# such as "disallow" or negated text containing "approve" later in the reply.
_ALLOW_WORDS = frozenset({"approve", "approved", "allow", "allowed", "yes"})
_DENY_WORDS = frozenset({"deny", "denied", "reject", "rejected", "no"})
_ALLOW_EMOJI = ("👍", "✅")
_DENY_EMOJI = ("👎", "❌")
_TOKEN_TRIM = ".,!?:;'\"()"


def _reply_intent(text: str) -> str | None:
    """Return allow/deny intent from the first word or emoji."""
    first = text.split()[0] if text.split() else ""
    if first.startswith(_ALLOW_EMOJI):
        return "allow"
    if first.startswith(_DENY_EMOJI):
        return "deny"
    word = first.strip(_TOKEN_TRIM).lower()
    if word in _ALLOW_WORDS:
        return "allow"
    if word in _DENY_WORDS:
        return "deny"
    return None


def resolve_from_reply(
    reply: str, resolve: Callable[[str, str], bool]
) -> bool | None:
    """Resolve the item referenced by a ``[d:<id>]`` token in an inbound reply."""
    m = _ID_TOKEN.search(reply or "")
    if not m:
        return None
    item_id = m.group(1)
    text = _ID_TOKEN.sub("", reply).strip()
    resolution = _reply_intent(text) or text
    return resolve(item_id, resolution)
