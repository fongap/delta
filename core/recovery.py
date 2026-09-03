"""Recovery Context (P3 §7.3 / §4.5 — 最小 Recovery Context).

The messages jsonl is the engine's source of truth for "what happened
in this session". It is enough to replay the turn, but on a hard
process kill (or a long-lived unattended session), a caller asking
"where was this session when it died?" has to scan the entire
messages list and re-derive the answer.

The Recovery Context is a small structured snapshot — the
"minimum state required to continue the task" the blueprint calls
out — persisted alongside the session so the next resume can answer
that question without re-parsing history, and so the UI can surface
"this run was paused at an approval on the GMail connector" without
rehydrating the engine.

Design contract:

- One snapshot per session, keyed by ``session_id``. Overwritten on
  every pause point (approval requested / ask_user / directory grant
  requested); the snapshot is always "what was happening at the
  last pause", not a history.
- Snapshot fields:

  - ``snapshot_at`` — ISO8601 UTC, for "how stale is this?"
  - ``run_id`` — the active run scope (matches ledger / artifacts)
  - ``session_id`` — for cross-check
  - ``phase`` — one of ``running`` / ``awaiting_approval`` /
    ``awaiting_question`` / ``awaiting_directory`` / ``awaiting_plan``;
    lets the UI render the right "you have a pending X" card without
    rehydrating the engine
  - ``pending_tool_call`` — ``{id, name, args_preview}`` of the
    in-flight tool call (or ``None`` if the turn is between calls)
  - ``pending_inbox_item_id`` — the Inbox item the run is waiting
    on (matches ``InboxItem.id``; the Inbox itself is the source of
    truth for resolution, the snapshot only records the link)
  - ``last_event_seq`` — ledger position at snapshot time; lets a
    resume skip "I already saw this" events when reattaching
  - ``todo_summary`` — last-known todo state (list of
    ``{content, status, activeForm}``); a compact copy so the UI can
    show "what was I doing" without re-running the model
  - ``recent_artifacts`` — last N (path, kind) produced by the run
  - ``error`` — last error string if the run crashed at a known
    point (else ``None``)

- The snapshot is **advisory** — the engine does not read it on
  resume. Resume still works from messages + Inbox + ledger alone
  (the existing contract). The snapshot is there to let callers
  inspect "where was this run" cheaply.

- Persisted on the session row (``sessions.recovery``) as JSON, in
  the same place as ``compaction`` / ``grants``. A schema-version
  integer (``schema=1``) sits at the top so future shape changes
  can be detected without parsing errors.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.jsonstate import load_json_state, save_json_state

# Phase constants: what was happening at the last pause. The phase is
# the one piece the UI needs to render the right "you have a pending
# X" card without rehydrating the engine. ``running`` covers the
# transient state between tool calls; the ``awaiting_*`` phases
# cover the durable pause points (the run is suspended on a prompt
# the user must answer).
PHASE_RUNNING = "running"
PHASE_AWAITING_APPROVAL = "awaiting_approval"
PHASE_AWAITING_QUESTION = "awaiting_question"
PHASE_AWAITING_DIRECTORY = "awaiting_directory"
PHASE_AWAITING_PLAN = "awaiting_plan"

# Stable union; explicit (not derived) so the docstring and
# callers have a single source of truth.
PHASES: tuple[str, ...] = (
    PHASE_RUNNING,
    PHASE_AWAITING_APPROVAL,
    PHASE_AWAITING_QUESTION,
    PHASE_AWAITING_DIRECTORY,
    PHASE_AWAITING_PLAN,
)

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PendingToolCall:
    """The in-flight tool call at the moment of the snapshot."""

    id: str
    name: str
    args_preview: str = ""  # args rendered as a single line (args_preview style)


@dataclass
class TodoItem:
    """A compact view of the agent's current todo list.

    Mirrors ``integrations/tools/todo.TodoItem`` but is duplicated
    here so the recovery module has no engine-layer dependency (it
    must be importable from anywhere without dragging the tool
    registry in). The producer (the engine wrapper) is responsible
    for shaping a real TodoItem into this form.
    """

    content: str
    status: str  # pending | in_progress | completed
    active_form: str = ""


@dataclass
class RecentArtifact:
    """A path + kind the run produced recently."""

    path: str  # workspace-relative
    kind: str  # md | pdf | xlsx | docx | txt | ...


@dataclass
class RecoverySnapshot:
    """One pause-point snapshot. See module docstring for the
    field-level contract."""

    schema: int = SCHEMA_VERSION
    snapshot_at: str = field(default_factory=_now)
    run_id: str = ""
    session_id: str = ""
    phase: str = PHASE_RUNNING
    pending_tool_call: PendingToolCall | None = None
    pending_inbox_item_id: str | None = None
    last_event_seq: int | None = None
    todo_summary: list[TodoItem] = field(default_factory=list)
    recent_artifacts: list[RecentArtifact] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # asdict already nests dataclass children; nothing to flatten.
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RecoverySnapshot:
        """Parse a snapshot back. Unknown future fields are dropped
        so an older reader can still load a newer snapshot (forward
        compat). A missing schema field is treated as v0 (legacy,
        pre-1.0 prototype) and accepted as-is — the field set is
        close enough that nothing critical breaks.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"snapshot must be a dict, got {type(raw).__name__}")
        schema = raw.get("schema", 0)
        if schema != SCHEMA_VERSION:
            # Future schema: we don't know what to do. Refuse rather
            # than silently misinterpret; the caller can fall back
            # to "no snapshot" semantics.
            raise ValueError(
                f"unsupported recovery snapshot schema: {schema!r} "
                f"(expected {SCHEMA_VERSION})"
            )
        ptc_raw = raw.get("pending_tool_call")
        ptc: PendingToolCall | None
        if ptc_raw is None:
            ptc = None
        elif isinstance(ptc_raw, dict):
            ptc = PendingToolCall(
                id=str(ptc_raw.get("id", "")),
                name=str(ptc_raw.get("name", "")),
                args_preview=str(ptc_raw.get("args_preview", "")),
            )
        else:
            raise ValueError("pending_tool_call must be a dict or None")
        todos_raw = raw.get("todo_summary") or []
        if not isinstance(todos_raw, list):
            raise ValueError("todo_summary must be a list")
        todos = [
            TodoItem(
                content=str(t.get("content", "")),
                status=str(t.get("status", "pending")),
                active_form=str(t.get("active_form", "")),
            )
            for t in todos_raw
            if isinstance(t, dict)
        ]
        arts_raw = raw.get("recent_artifacts") or []
        if not isinstance(arts_raw, list):
            raise ValueError("recent_artifacts must be a list")
        arts = [
            RecentArtifact(
                path=str(a.get("path", "")),
                kind=str(a.get("kind", "")),
            )
            for a in arts_raw
            if isinstance(a, dict)
        ]
        phase = str(raw.get("phase", PHASE_RUNNING))
        if phase not in PHASES:
            raise ValueError(f"unknown phase: {phase!r}")
        return cls(
            schema=schema,
            snapshot_at=str(raw.get("snapshot_at", _now())),
            run_id=str(raw.get("run_id", "")),
            session_id=str(raw.get("session_id", "")),
            phase=phase,
            pending_tool_call=ptc,
            pending_inbox_item_id=(
                str(raw["pending_inbox_item_id"])
                if raw.get("pending_inbox_item_id") is not None
                else None
            ),
            last_event_seq=(
                int(raw["last_event_seq"])
                if raw.get("last_event_seq") is not None
                else None
            ),
            todo_summary=todos,
            recent_artifacts=arts,
            error=(str(raw["error"]) if raw.get("error") is not None else None),
        )


class RecoveryStore:
    """The recovery sidecar: per-session snapshots, persisted as a
    single JSON file. One file per data dir is fine because snapshot
    volume is tiny (one record per session) and reads/writes happen
    only at pause points (not per tool call)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._snapshots: dict[str, RecoverySnapshot] = {}
        self._load()

    def _load(self) -> None:
        if self.path and self.path.is_file():
            data = load_json_state(self.path, {}) or {}
            for sid, raw in (data.get("sessions") or {}).items():
                try:
                    self._snapshots[sid] = RecoverySnapshot.from_dict(raw)
                except ValueError:
                    # A future-schema snapshot is not our problem to
                    # interpret; leave it in the file (we won't
                    # overwrite it on save) and skip in memory.
                    pass

    def _save(self) -> None:
        if not self.path:
            return
        # Re-serialize everything: future-schema entries we skipped
        # on load need to survive a write, so we round-trip via the
        # raw JSON rather than letting RecoverySnapshot.from_dict
        # drop them.
        if self.path.is_file():
            existing = load_json_state(self.path, {}) or {}
        else:
            existing = {}
        sessions_raw: dict[str, Any] = dict(existing.get("sessions") or {})
        for sid, snap in self._snapshots.items():
            sessions_raw[sid] = snap.to_dict()
        save_json_state(self.path, {"sessions": sessions_raw})

    def write(self, snapshot: RecoverySnapshot) -> None:
        """Record or replace the snapshot for one session.

        Validation: ``session_id`` must match a real session; the
        caller is the engine wrapper, which already knows the
        session id. ``phase`` must be one of the documented phases.
        ``run_id`` is required at non-running phases (you can't be
        awaiting an approval if there's no run).
        """
        if not snapshot.session_id:
            raise ValueError("snapshot.session_id is required")
        if snapshot.phase not in PHASES:
            raise ValueError(f"unknown phase: {snapshot.phase!r}")
        if snapshot.phase != PHASE_RUNNING and not snapshot.run_id:
            raise ValueError(
                f"non-running phase {snapshot.phase!r} requires run_id"
            )
        with self._lock:
            self._snapshots[snapshot.session_id] = snapshot
            self._save()

    def clear(self, session_id: str) -> bool:
        """Drop the snapshot for one session (e.g. when the run
        completes normally and the snapshot is no longer useful).
        Returns True if a snapshot was present and removed."""
        with self._lock:
            if session_id not in self._snapshots:
                return False
            del self._snapshots[session_id]
            self._save()
            return True

    def get(self, session_id: str) -> RecoverySnapshot | None:
        return self._snapshots.get(session_id)

    def latest(self) -> list[RecoverySnapshot]:
        """All current snapshots, newest first. Used by the UI to
        surface the cross-session "things awaiting your attention"
        list — an approval requested by an unattended session is
        in the Inbox already, but the snapshot adds structured
        context (what tool, what args) without the caller having
        to rehydrate each engine."""
        with self._lock:
            return sorted(
                self._snapshots.values(),
                key=lambda s: s.snapshot_at,
                reverse=True,
            )
