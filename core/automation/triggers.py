"""Event-driven automation trigger model and dispatcher."""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("core.automation.triggers")


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


SOURCE_MANUAL = "manual"
SOURCE_FILESYSTEM = "filesystem"
SOURCE_INBOX = "inbox"

SOURCES = (SOURCE_MANUAL, SOURCE_FILESYSTEM, SOURCE_INBOX)


def _validate_condition(cond: dict[str, Any]) -> None:
    """Reject malformed trigger conditions before they reach dispatch."""
    if not isinstance(cond, dict):
        raise ValueError(f"trigger condition must be a dict, got {type(cond).__name__}")
    src = cond.get("source")
    if src not in SOURCES:
        raise ValueError(
            f"unknown trigger source: {src!r}; expected one of {SOURCES}"
        )
    if src == SOURCE_FILESYSTEM and not cond.get("glob"):
        raise ValueError("filesystem trigger needs a 'glob' field")
    if src == SOURCE_INBOX and not cond.get("kind"):
        raise ValueError("inbox trigger needs a 'kind' field")


@dataclass
class Trigger:
    """Event-driven counterpart of ``Schedule`` with a refire cooldown."""

    source: str
    condition: dict[str, Any] = field(default_factory=dict)
    cooldown_seconds: float = 60.0
    last_fired_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "condition": dict(self.condition),
            "cooldown_seconds": self.cooldown_seconds,
            "last_fired_at": self.last_fired_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Trigger:
        return cls(
            source=d.get("source", SOURCE_MANUAL),
            condition=dict(d.get("condition") or {}),
            cooldown_seconds=float(d.get("cooldown_seconds", 60.0)),
            last_fired_at=d.get("last_fired_at"),
        )


class TriggerRegistry:
    """Track triggers and return task ids matching an incoming event."""

    def __init__(self) -> None:
        self._by_task: dict[str, Trigger] = {}
        self._lock = threading.RLock()
        self._recent: dict[tuple[str, str], float] = {}

    def add(self, task_id: str, trigger: Trigger) -> None:
        with self._lock:
            self._by_task[task_id] = trigger

    def remove(self, task_id: str) -> bool:
        with self._lock:
            return self._by_task.pop(task_id, None) is not None

    def get(self, task_id: str) -> Trigger | None:
        return self._by_task.get(task_id)

    def list(self) -> dict[str, Trigger]:
        with self._lock:
            return dict(self._by_task)

    def clear(self) -> None:
        with self._lock:
            self._by_task.clear()
            self._recent.clear()

    def dispatch(self, event: dict[str, Any]) -> list[str]:
        """Return task ids whose trigger matches the event; malformed events fail closed."""
        with self._lock:
            matches: list[tuple[str, Trigger]] = []
            try:
                src = event.get("source")
                if not src:
                    logger.warning("trigger dispatch: missing source")
                    return []
                now = _now()
                for tid, trigger in self._by_task.items():
                    if trigger.source != src:
                        continue
                    if not self._matches(trigger, event):
                        continue
                    if trigger.last_fired_at and (
                        now - trigger.last_fired_at < trigger.cooldown_seconds
                    ):
                        continue
                    fp = self._fingerprint(tid, event)
                    recent_at = self._recent.get(fp)
                    if recent_at and now - recent_at < trigger.cooldown_seconds:
                        continue
                    self._recent[fp] = now
                    trigger.last_fired_at = now
                    matches.append((tid, trigger))
                cutoff = now - 600.0
                for k in list(self._recent):
                    if self._recent[k] < cutoff:
                        self._recent.pop(k, None)
            except Exception:
                logger.exception("trigger dispatch failed")
                return []
        return [tid for tid, _ in matches]

    @staticmethod
    def _matches(trigger: Trigger, event: dict[str, Any]) -> bool:
        src = trigger.source
        cond = trigger.condition
        if src == SOURCE_MANUAL:
            target = cond.get("task_id")
            return target is None or target == event.get("task_id")
        if src == SOURCE_FILESYSTEM:
            glob = cond.get("glob", "")
            event_path = event.get("path", "")
            if not event_path or not glob:
                return False
            try:
                if fnmatch.fnmatch(event_path, glob):
                    return True
            except Exception:
                pass
            try:
                return Path(event_path).resolve().is_relative_to(Path(glob).resolve())
            except Exception:
                return False
        if src == SOURCE_INBOX:
            expected_kind = cond.get("kind")
            if expected_kind and event.get("kind") != expected_kind:
                return False
            data_match = cond.get("data_match") or {}
            ev_data = event.get("data") or {}
            for k, v in data_match.items():
                if ev_data.get(k) != v:
                    return False
            return True
        return False

    @staticmethod
    def _fingerprint(task_id: str, event: dict[str, Any]) -> tuple[str, str]:
        """Return the deduplication key for one task/event pair."""
        src = event.get("source", "")
        if src == SOURCE_MANUAL:
            return (task_id, "manual")
        if src == SOURCE_FILESYSTEM:
            return (task_id, f"fs:{event.get('path', '')}")
        if src == SOURCE_INBOX:
            return (task_id, f"inbox:{event.get('id', '')}")
        return (task_id, src)

    def hydrate_from_store(self, tasks: list) -> None:
        """Replace the registry with valid triggers from persisted tasks."""
        with self._lock:
            self._by_task.clear()
            self._recent.clear()
            for task in tasks:
                trig = getattr(task, "trigger", None)
                if trig is None:
                    continue
                if not isinstance(trig, Trigger):
                    continue
                self._by_task[task.id] = trig


__all__ = (
    "Trigger",
    "TriggerRegistry",
    "SOURCE_MANUAL",
    "SOURCE_FILESYSTEM",
    "SOURCE_INBOX",
    "SOURCES",
    "_validate_condition",
)
