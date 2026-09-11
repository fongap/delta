"""R5.1 Live Steering — control channel for active runs.

Provides three distinct user-intent semantics that are NOT unified into
"just another message":

* **Steer** — modify the current run's direction mid-execution.
  Uses the same ``run_id`` (no new run). Applied at a safe point.
* **Follow-up** — queue work for after the current run completes.
  Does not affect the current run at all.
* **Cancel** — stop the current run.

The control channel is bound to an ``active_run_id`` and is a
supervision channel, not a second chat session.

Ledger integration (B6): steering events are recorded as
``user.steer.requested``, ``user.steer.accepted``, ``user.steer.deferred``,
``user.steer.applied``, ``user.steer.rejected`` so the entire human
control history is auditable.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SteerState(str, Enum):
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    APPLIED = "applied"
    REJECTED = "rejected"


class SteerSafePoint(str, Enum):
    """When a steer should be applied relative to in-flight work."""
    APPLY_NOW = "apply_now"
    DEFER_TO_SAFE_POINT = "defer_to_safe_point"
    PAUSE_BEFORE_CONSEQUENCE = "pause_before_consequence"
    REJECT_ALREADY_OCCURRED = "reject_already_occurred"


@dataclass
class PendingSteer:
    steer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    session_id: str = ""
    content: str = ""
    source: str = "user"
    created_at: float = field(default_factory=time.time)
    state: SteerState = SteerState.REQUESTED
    safe_point: SteerSafePoint | None = None
    applied_at: float | None = None
    reason: str | None = None


@dataclass
class FollowUpItem:
    followup_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    content: str = ""
    created_at: float = field(default_factory=time.time)
    position: int = 0


class SteerQueue:
    """In-memory pending steer queue for one active run.

    Thread-safe. The engine's turn loop checks ``poll()`` at safe points
    (between tool calls, between model requests) to see if a steer is
    waiting.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._steers: list[PendingSteer] = []
        self._lock = threading.Lock()

    def submit(self, content: str, *, source: str = "user",
               session_id: str = "") -> PendingSteer:
        steer = PendingSteer(
            run_id=self.run_id,
            session_id=session_id,
            content=content,
            source=source,
        )
        with self._lock:
            self._steers.append(steer)
        return steer

    def poll(self) -> PendingSteer | None:
        """Get the next requested steer (if any) for safe-point application."""
        with self._lock:
            for s in self._steers:
                if s.state == SteerState.REQUESTED:
                    return s
            return None

    def accept(self, steer_id: str) -> bool:
        with self._lock:
            for s in self._steers:
                if s.steer_id == steer_id and s.state == SteerState.REQUESTED:
                    s.state = SteerState.ACCEPTED
                    return True
            return False

    def defer(self, steer_id: str, *, safe_point: SteerSafePoint) -> bool:
        with self._lock:
            for s in self._steers:
                if s.steer_id == steer_id and s.state in (SteerState.REQUESTED, SteerState.ACCEPTED):
                    s.state = SteerState.DEFERRED
                    s.safe_point = safe_point
                    return True
            return False

    def apply(self, steer_id: str) -> bool:
        with self._lock:
            for s in self._steers:
                if s.steer_id == steer_id and s.state in (SteerState.REQUESTED, SteerState.ACCEPTED, SteerState.DEFERRED):
                    s.state = SteerState.APPLIED
                    s.applied_at = time.time()
                    return True
            return False

    def reject(self, steer_id: str, *, reason: str) -> bool:
        with self._lock:
            for s in self._steers:
                if s.steer_id == steer_id and s.state != SteerState.APPLIED:
                    s.state = SteerState.REJECTED
                    s.reason = reason
                    return True
            return False

    def list(self) -> list[PendingSteer]:
        with self._lock:
            return list(self._steers)

    def has_pending(self) -> bool:
        with self._lock:
            return any(s.state == SteerState.REQUESTED for s in self._steers)


class FollowUpQueue:
    """In-memory follow-up queue for a run.

    Follow-ups are separate from steers: they do NOT affect the current
    run. They are executed sequentially after the current run completes.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._items: list[FollowUpItem] = []
        self._lock = threading.Lock()

    def add(self, content: str) -> FollowUpItem:
        with self._lock:
            item = FollowUpItem(
                run_id=self.run_id,
                content=content,
                position=len(self._items),
            )
            self._items.append(item)
            return item

    def remove(self, followup_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.followup_id != followup_id]
            return len(self._items) < before

    def reorder(self, followup_id: str, new_position: int) -> bool:
        with self._lock:
            item = next((i for i in self._items if i.followup_id == followup_id), None)
            if item is None:
                return False
            self._items.remove(item)
            self._items.insert(new_position, item)
            for idx, it in enumerate(self._items):
                it.position = idx
            return True

    def list(self) -> list[FollowUpItem]:
        with self._lock:
            return list(self._items)

    def drain(self) -> list[FollowUpItem]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items


class RunControlChannel:
    """Control channel for one active run.

    Binds to ``active_run_id`` and accepts Steer / Follow-up / Cancel.
    This is a supervision channel, not a second chat session.
    """

    def __init__(self, run_id: str, session_id: str = "",
                 ledger: Any = None, workspace: str = "") -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.ledger = ledger
        self.workspace = workspace
        self.steers = SteerQueue(run_id)
        self.followups = FollowUpQueue(run_id)
        self._cancelled = threading.Event()

    def steer(self, content: str, *, source: str = "user") -> PendingSteer:
        """Submit a steer — modify current run direction mid-execution."""
        steer = self.steers.submit(content, source=source, session_id=self.session_id)
        if self.ledger is not None:
            self._ledger_event("user.steer.requested", {
                "steer_id": steer.steer_id,
                "run_id": self.run_id,
                "source": source,
            })
        return steer

    def follow_up(self, content: str) -> FollowUpItem:
        """Queue a follow-up — work to do after the current run completes."""
        return self.followups.add(content)

    def cancel(self) -> None:
        """Cancel the current run."""
        self._cancelled.set()
        if self.ledger is not None:
            self._ledger_event("user.cancel.requested", {
                "run_id": self.run_id,
            })

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def apply_steer(self, steer_id: str, *, safe_point: SteerSafePoint) -> bool:
        """Mark a steer as applied at a safe point."""
        ok = self.steers.apply(steer_id)
        if ok and self.ledger is not None:
            self._ledger_event("user.steer.applied", {
                "steer_id": steer_id,
                "run_id": self.run_id,
                "safe_point": safe_point.value,
                "applied_at": time.time(),
            })
        return ok

    def reject_steer(self, steer_id: str, *, reason: str) -> bool:
        """Reject a steer because the action already occurred."""
        ok = self.steers.reject(steer_id, reason=reason)
        if ok and self.ledger is not None:
            self._ledger_event("user.steer.rejected", {
                "steer_id": steer_id,
                "run_id": self.run_id,
                "reason": reason,
            })
        return ok

    def _ledger_event(self, event_type: str, payload: dict) -> None:
        try:
            if hasattr(self.ledger, "append"):
                self.ledger.append(
                    self.run_id, event_type, "user",
                    time.time(), payload, self.workspace or "",
                )
        except Exception:
            pass


# Global registry of active control channels (one per active run).
_channels: dict[str, RunControlChannel] = {}
_channels_lock = threading.Lock()


def get_control_channel(run_id: str) -> RunControlChannel | None:
    with _channels_lock:
        return _channels.get(run_id)


def create_control_channel(run_id: str, **kwargs) -> RunControlChannel:
    with _channels_lock:
        ch = RunControlChannel(run_id, **kwargs)
        _channels[run_id] = ch
        return ch


def remove_control_channel(run_id: str) -> None:
    with _channels_lock:
        _channels.pop(run_id, None)
