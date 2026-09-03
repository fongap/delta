"""Conditional triggers — event-driven Automation dispatch (P3 §7.3 §734).

The §7.2 Automation path is purely time-based: a Schedule computes
``next_run`` and the Scheduler ticks. §7.3 extends that to
"Trigger" — the same Task / Run Runtime (no second execution
model), but firing on an **event** rather than a clock.

A task gets one of:
  - ``Schedule`` (cron / once) — the existing time-based path.
  - ``Trigger`` (this module) — event-driven: filesystem matches,
    Inbox item with matching data, or a manual push from a tool.
    The same `Scheduler` and the same `_run_scheduled_task` path
    handle both — the trigger just chooses *when* to call them,
    not *how*.

Three trigger sources in v1:

- ``"manual"`` — a test/dev or automation-tool surface calls
  :meth:`TriggerRegistry.dispatch` with an explicit payload. The
  payload's ``task_id`` (or a configured mapping) routes to the
  right task. This is also the entry point for higher-level
  surfaces (the future connector-event bridge).
- ``"filesystem"`` — a glob on the task's workspace (or a path
  inside it). A match is a ``FileEvent`` and a glob hit dispatches.
- ``"inbox"`` — a kind + optional ``data`` filter on Inbox items.
  Used for "run when an approval with a specific task_id comes in"
  style chains (later PR).

Each task holds either a ``Schedule`` (legacy) OR a ``Trigger``;
they don't mix. The store persists ``trigger`` in the JSON blob
alongside the legacy ``schedule``; ``compute_next_run`` continues
to work for ``kind: "cron" / "once"`` and returns ``None`` for
``"condition"`` triggers (they don't have a next run time).
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("core.automation.triggers")


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


# -- condition spec --------------------------------------------------------
# A small, JSON-friendly dict shape that lives on the task record. v1
# supports three sources; new ones extend the same dict with another
# ``source`` discriminator (and a small evaluator below).


SOURCE_MANUAL = "manual"
SOURCE_FILESYSTEM = "filesystem"
SOURCE_INBOX = "inbox"

SOURCES = (SOURCE_MANUAL, SOURCE_FILESYSTEM, SOURCE_INBOX)


def _validate_condition(cond: dict[str, Any]) -> None:
    """Reject malformed condition specs at save time. Bad shape is a
    store-level bug, not a runtime fallback."""
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


# -- Trigger model ---------------------------------------------------------


@dataclass
class Trigger:
    """The event-driven counterpart of :class:`Schedule`.

    The condition is a small dict (see :data:`SOURCES`); ``cooldown``
    prevents rapid re-firing on the same event stream (filesystem
    watchers can emit dozens of events for one logical action;
    without a cooldown, a task configured to "process new files
    dropped in ``~/inbox``" would run dozens of times).
    """

    source: str  # "manual" | "filesystem" | "inbox"
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


# -- TriggerRegistry --------------------------------------------------------
# In-memory registry: task_id -> Trigger. The store persists the JSON
# blob; the registry is the dispatcher the Scheduler / external callers
# interact with. The registry does NOT call the scheduler directly —
# it returns a list of task_ids to run, and the caller (the scheduler's
# tick or a tool's "fire now" action) decides how to run them. This
# keeps the trigger layer decoupled from the dispatch model.


class TriggerRegistry:
    """Tracks event-driven triggers and routes events to tasks.

    Designed to be used by:
    - the scheduler (e.g. as an ``extra_tick`` that polls filesystem
      and dispatches hits), or
    - tools / events surface (which call :meth:`dispatch` directly
      with a pre-shaped event payload).
    """

    def __init__(self) -> None:
        self._by_task: dict[str, Trigger] = {}
        self._lock = threading.RLock()
        # Recent events by (task_id, event_fingerprint) — for dedup.
        # In-memory only: cross-restart dedup is a future concern (the
        # task's ``last_fired_at`` already covers the cooldown window).
        self._recent: dict[tuple[str, str], float] = {}

    # -- registration ---------------------------------------------------------
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

    # -- dispatch -------------------------------------------------------------
    def dispatch(self, event: dict[str, Any]) -> list[str]:
        """Route an event to the tasks whose trigger matches it.

        The event must carry a ``source`` field that matches the
        trigger's source — ``"manual"`` for an explicit tool call,
        ``"filesystem"`` for a file event, ``"inbox"`` for an inbox
        item. The registry never raises; bad events are dropped with
        a warning so the dispatch path can't tear down the scheduler.

        Returns the list of task_ids that should be run (the caller
        is responsible for actually running them — the registry only
        decides *who* matches).
        """
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
                        # Cooldown gate: same trigger fired too recently.
                        # This is a per-trigger dedup, distinct from
                        # the per-(task, event) dedup below.
                        continue
                    fp = self._fingerprint(tid, event)
                    recent_at = self._recent.get(fp)
                    if recent_at and now - recent_at < trigger.cooldown_seconds:
                        continue
                    self._recent[fp] = now
                    trigger.last_fired_at = now
                    matches.append((tid, trigger))
                # Trim recent dedup map so it doesn't grow unbounded.
                cutoff = now - 600.0
                for k in list(self._recent):
                    if self._recent[k] < cutoff:
                        self._recent.pop(k, None)
            except Exception:
                logger.exception("trigger dispatch failed")
                return []
        return [tid for tid, _ in matches]

    # -- matching -------------------------------------------------------------
    @staticmethod
    def _matches(trigger: Trigger, event: dict[str, Any]) -> bool:
        """Per-source matching. Each branch returns True iff the
        event satisfies the trigger's condition."""
        src = trigger.source
        cond = trigger.condition
        if src == SOURCE_MANUAL:
            # Manual triggers match on a task_id carried in the event.
            target = cond.get("task_id")
            return target is None or target == event.get("task_id")
        if src == SOURCE_FILESYSTEM:
            glob = cond.get("glob", "")
            event_path = event.get("path", "")
            if not event_path or not glob:
                return False
            # Match either as a glob (event_path matches glob pattern)
            # or as a prefix (glob is a directory prefix). fnmatch
            # handles wildcards; the prefix branch is for the common
            # case of "any new file under this dir".
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
            # data_match: subset check (every expected key/value must
            # equal the event's data). A trigger configured with
            # data_match={"task_id": "t-1"} fires only for inbox
            # items whose data has task_id == "t-1".
            data_match = cond.get("data_match") or {}
            ev_data = event.get("data") or {}
            for k, v in data_match.items():
                if ev_data.get(k) != v:
                    return False
            return True
        return False

    @staticmethod
    def _fingerprint(task_id: str, event: dict[str, Any]) -> tuple[str, str]:
        """A short key for the per-(task, event) dedup window.

        Two events with the same fingerprint within the cooldown
        window are treated as duplicates (e.g. an FS watcher that
        emits several events for the same write).
        """
        src = event.get("source", "")
        if src == SOURCE_MANUAL:
            return (task_id, "manual")
        if src == SOURCE_FILESYSTEM:
            return (task_id, f"fs:{event.get('path', '')}")
        if src == SOURCE_INBOX:
            return (task_id, f"inbox:{event.get('id', '')}")
        return (task_id, src)

    # -- persistence ----------------------------------------------------------
    def hydrate_from_store(self, tasks: list) -> None:
        """Load triggers from a list of task records.

        ``tasks`` is a list of :class:`ScheduledTask` (or any object
        with ``.id`` and ``.trigger`` attributes). Tasks without a
        trigger are skipped; tasks with a malformed trigger are
        skipped with a warning (a corrupted row must not break the
        whole registry).
        """
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
