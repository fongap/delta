"""Persistent automation task, schedule, trigger, and run models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Cron day-of-week uses Sunday=0/7 and Monday=1.
_DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _make_trigger_or_none(d: dict | None) -> Any:
    if not d:
        return None
    from core.automation.triggers import Trigger

    return Trigger.from_dict(d)


def _now() -> float:
    return time.time()


def rule_entry(tool: str, target: str | None = None) -> str:
    return f"{tool} {target}" if target else tool


def rule_parts(entry: str) -> tuple[str, str | None]:
    tool, _, target = entry.strip().partition(" ")
    return tool, (target.strip() or None)


def grant_entries(permissions: Any) -> list[str]:
    """Return target-bound write permissions that are safe to persist."""
    from integrations.connectors.tool_defs import target_arg_for

    entries: list[str] = []
    for item in permissions or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("access", "")).lower() != "write":
            continue
        tool = str(item.get("tool", "")).strip()
        target = str(item.get("target", "")).strip()
        if not tool or not target or target_arg_for(tool) is None:
            continue
        entry = rule_entry(tool, target)
        if entry not in entries:
            entries.append(entry)
    return entries


def _human_time(hour: int, minute: int) -> str:
    ampm = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12}:{minute:02d} {ampm}"


@dataclass
class Schedule:
    kind: str  # "cron" | "once"
    cron: str | None = None
    fire_at: str | None = None
    timezone: str = "local"

    def human(self) -> str:
        """Return a best-effort human label, falling back to raw cron."""
        if self.kind == "once":
            return f"Once at {self.fire_at}"
        parts = (self.cron or "").split()
        if len(parts) != 5:
            return self.cron or "?"
        minute, hour, dom, month, dow = parts
        try:
            t = _human_time(int(hour), int(minute))
        except ValueError:
            return self.cron or "?"
        if dom == "*" and dow == "*":
            return f"Every day at ~{t}"
        if dom == "*" and dow.isdigit():
            return f"Every {_DOW[int(dow) % 7]} at ~{t}"
        if dom.isdigit() and dow == "*":
            return f"Monthly on day {dom} at ~{t}"
        return self.cron or "?"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "cron": self.cron,
            "fire_at": self.fire_at,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Schedule:
        return cls(
            kind=d.get("kind", "cron"),
            cron=d.get("cron"),
            fire_at=d.get("fire_at"),
            timezone=d.get("timezone", "local"),
        )


@dataclass
class ScheduledTask:
    title: str
    instructions: str
    schedule: Schedule
    workspace: str
    origin_surface: str = "delta"
    origin_session_id: str = ""
    agent: str = "delta"
    id: str = field(default_factory=lambda: "task-" + uuid.uuid4().hex[:10])
    task_session_id: str = ""
    model: str | None = None
    notify_on_completion: bool = True
    notify_target: str | None = None
    always_allowed_tools: list[str] = field(default_factory=list)
    always_allowed_commands: list[str] = field(default_factory=list)
    enabled: bool = True
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    next_run: float | None = None
    last_run: float | None = None
    last_status: str | None = None
    run_count: int = 0
    max_runs: int | None = None
    seen_runs_at: float = 0.0
    # Stored as a plain dict and converted to ValidationCriteria at runtime.
    # None uses the safe default completion criteria.
    validation_criteria: dict | None = None
    # Event-driven tasks use a trigger instead of their time-based schedule.
    trigger: Any = field(default=None)

    def __post_init__(self) -> None:
        if not self.task_session_id:
            self.task_session_id = f"__task__{self.id}"

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["schedule"] = self.schedule.to_dict()
        trig = d.get("trigger")
        if trig is not None and hasattr(trig, "to_dict"):
            d["trigger"] = trig.to_dict()
        else:
            d["trigger"] = trig
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ScheduledTask:
        d = dict(d)
        d["schedule"] = Schedule.from_dict(d.get("schedule") or {})
        d["trigger"] = _make_trigger_or_none(d.get("trigger"))
        return cls(**d)

    def standing_rules(self) -> dict[str, set[str]]:
        """Return target-bound grants as ``{tool: {targets}}``."""
        out: dict[str, set[str]] = {}
        for entry in self.always_allowed_tools:
            tool, target = rule_parts(entry)
            if tool and target:
                out.setdefault(tool, set()).add(target)
        return out

    def name_allowed_tools(self) -> set[str]:
        """Return legacy name-only grants without target binding."""
        return {
            tool
            for tool, target in map(rule_parts, self.always_allowed_tools)
            if tool and target is None
        }

    def add_rule(self, tool: str, target: str) -> bool:
        entry = rule_entry(tool, target)
        if not tool or not target or entry in self.always_allowed_tools:
            return False
        self.always_allowed_tools.append(entry)
        return True

    def revoke_rule(self, entry: str) -> bool:
        if entry in self.always_allowed_tools:
            self.always_allowed_tools.remove(entry)
            return True
        return False

    def public(self) -> dict[str, Any]:
        """Return the secret-free API/UI representation."""
        return {
            "id": self.id,
            "title": self.title,
            "instructions": self.instructions,
            "schedule": self.schedule.human(),
            "schedule_raw": self.schedule.to_dict(),
            "workspace": self.workspace,
            "agent": self.agent,
            "enabled": self.enabled,
            "next_run": self.next_run,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "run_count": self.run_count,
            "notify_on_completion": self.notify_on_completion,
            "seen_runs_at": self.seen_runs_at,
            "always_allowed": [
                {"entry": e, "tool": t, "target": tg}
                for e, (t, tg) in (
                    (e, rule_parts(e)) for e in sorted(set(self.always_allowed_tools))
                )
            ],
        }


@dataclass
class TaskRun:
    task_id: str
    run_id: str = field(default_factory=lambda: "run-" + uuid.uuid4().hex[:10])
    started_at: float = field(default_factory=_now)
    finished_at: float | None = None
    status: str = "running"  # running | ok | error | skipped | validation_failed
    result_text: str | None = None
    artifacts: list[dict] = field(default_factory=list)
    error: str | None = None
    trigger: str = "schedule"  # schedule | manual | catchup
    session_id: str = ""
    # Denormalized on each run so workspace queries do not need a task join.
    workspace: str = ""

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = f"__run__{self.run_id}"

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> TaskRun:
        # Older rows stored artifact paths as strings. Preserve read compatibility
        # by upgrading them to the minimum artifact shape in memory.
        if "artifacts" in d and d["artifacts"] and isinstance(d["artifacts"][0], str):
            d = dict(d)
            d["artifacts"] = [
                {
                    "path": p,
                    "name": p.rsplit("/", 1)[-1],
                    "kind": "text",
                    "size": 0,
                    "modified_at": 0.0,
                    "run_id": d.get("run_id", ""),
                    "sha256": None,
                    "incomplete": True,
                }
                for p in d["artifacts"]
            ]
        return cls(**d)
