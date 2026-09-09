"""Recovery Checkpoint — Rust-authoritative pause-point snapshot.

ADR-029 R2 Checkpoint Hard-Cut: Rust ``delta_core`` is the sole authority
for all checkpoint writes, reads, and validation. Python retains only the
public adapter used by the current runtime and the stable value types from
the v0.3.2 contract.

The snapshot schema mirrors the Rust ``CheckpointRecord``:
- schema: int (currently 1)
- created_at: ISO8601 UTC
- run_id: str
- session_id: str
- phase: one of ``running`` / ``awaiting_approval`` / ``awaiting_question``
  / ``awaiting_directory`` / ``awaiting_plan``
- pending_tool_call: ``{id, name, args_preview}`` or ``None``
- pending_inbox_item_id: str or ``None``
- last_event_seq: int or ``None``
- todo_summary: list of ``{content, status, active_form}``
- recent_artifacts: list of ``{path, kind}``
- error: str or ``None``
- snapshot_hash: sha256 of canonical snapshot payload
- recoverable: bool (true iff phase is a paused/awaiting phase)
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

# Phase constants: what was happening at the last pause.
PHASE_RUNNING = "running"
PHASE_AWAITING_APPROVAL = "awaiting_approval"
PHASE_AWAITING_QUESTION = "awaiting_question"
PHASE_AWAITING_DIRECTORY = "awaiting_directory"
PHASE_AWAITING_PLAN = "awaiting_plan"

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
    args_preview: str = ""


@dataclass
class TodoItem:
    """A compact view of the agent's current todo list."""

    content: str
    status: str
    active_form: str = ""


@dataclass
class RecentArtifact:
    """A path + kind the run produced recently."""

    path: str
    kind: str


@dataclass
class RecoverySnapshot:
    """One pause-point snapshot. See module docstring for the field contract."""

    schema: int = SCHEMA_VERSION
    created_at: str = field(default_factory=_now)
    run_id: str = ""
    session_id: str = ""
    phase: str = PHASE_RUNNING
    pending_tool_call: PendingToolCall | None = None
    pending_inbox_item_id: str | None = None
    last_event_seq: int | None = None
    todo_summary: list[TodoItem] = field(default_factory=list)
    recent_artifacts: list[RecentArtifact] = field(default_factory=list)
    error: str | None = None
    # Rust-authoritative fields (populated on read, not required on write)
    checkpoint_id: str | None = None
    snapshot_hash: str | None = None
    recoverable: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.pending_tool_call is not None:
            d["pending_tool_call"] = asdict(self.pending_tool_call)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RecoverySnapshot:
        """Parse a checkpoint record from Rust into the Python snapshot type."""
        if not isinstance(raw, dict):
            raise ValueError(f"checkpoint must be a dict, got {type(raw).__name__}")
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
            schema=int(raw.get("schema", SCHEMA_VERSION)),
            created_at=str(raw.get("created_at", _now())),
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
            error=(
                str(raw["error"]) if raw.get("error") is not None else None
            ),
            checkpoint_id=(
                str(raw["id"]) if raw.get("id") is not None else None
            ),
            snapshot_hash=(
                str(raw["snapshot_hash"]) if raw.get("snapshot_hash") is not None else None
            ),
            recoverable=bool(raw.get("recoverable", False)),
        )


class CheckpointAuthorityError(RuntimeError):
    """Raised when the checkpoint authority (Rust delta_core) fails."""


class RecoveryStore:
    """Thin facade over Rust delta_core checkpoint authority.

    All checkpoint persistence, reads, and validation are delegated to
    the Rust ``delta_core`` process via the unified ``DeltaCoreClient``.
    The SQLite database (``run_events.db``) is opened and managed entirely
    by the Rust side; Python never touches it directly.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(self.db_path)
        self._lock = threading.RLock()
        self._client: DeltaCoreClient | None = None

    def _client_get(self) -> DeltaCoreClient:
        if self._client is None:
            self._client = DeltaCoreClient()
        return self._client

    def _invoke(self, cmd: str, **kwargs: Any) -> Any:
        client = self._client_get()
        try:
            return client.command({"cmd": cmd, "db": self._db_path, **kwargs})
        except DeltaCoreError as e:
            raise CheckpointAuthorityError(f"checkpoint {cmd}: {e}") from e

    def write(self, snapshot: RecoverySnapshot) -> None:
        """Record or replace the checkpoint for one session.

        Delegates to ``checkpoint.register`` in Rust. The Rust authority
        generates the checkpoint_id, snapshot_hash, and recoverable flag.
        """
        if not snapshot.session_id:
            raise ValueError("snapshot.session_id is required")
        if snapshot.phase not in PHASES:
            raise ValueError(f"unknown phase: {snapshot.phase!r}")
        if snapshot.phase != PHASE_RUNNING and not snapshot.run_id:
            raise ValueError(
                f"non-running phase {snapshot.phase!r} requires run_id"
            )

        pending_tool_call = None
        if snapshot.pending_tool_call is not None:
            pending_tool_call = {
                "id": snapshot.pending_tool_call.id,
                "name": snapshot.pending_tool_call.name,
                "args_preview": snapshot.pending_tool_call.args_preview,
            }

        todo_summary = [
            {"content": t.content, "status": t.status, "active_form": t.active_form}
            for t in snapshot.todo_summary
        ]
        recent_artifacts = [
            {"path": a.path, "kind": a.kind} for a in snapshot.recent_artifacts
        ]

        self._invoke(
            "checkpoint.register",
            run_id=snapshot.run_id,
            session_id=snapshot.session_id,
            phase=snapshot.phase,
            pending_tool_call=pending_tool_call,
            pending_inbox_item_id=snapshot.pending_inbox_item_id,
            last_event_seq=snapshot.last_event_seq,
            todo_summary=todo_summary,
            recent_artifacts=recent_artifacts,
            error=snapshot.error,
        )

    def clear(self, session_id: str) -> bool:
        """Drop the checkpoint for one session (e.g. when the run completes).

        Checkpoints are immutable once written; this is a no-op for the
        authority but kept for API compatibility. Returns True for
        backward compatibility with callers expecting a boolean.
        """
        # Checkpoints are append-only facts in the ledger. We don't delete.
        # Return True to signal "acknowledged" without mutating authority.
        return True

    def get(self, session_id: str) -> RecoverySnapshot | None:
        """Get the latest checkpoint for a session (by run_id lookup).

        Note: The Rust authority keys by run_id, not session_id directly.
        This method finds the latest checkpoint for the session's run.
        """
        # We don't have run_id here; list all and filter by session_id.
        # This is a legacy compatibility path — callers should prefer
        # passing run_id if available.
        result = self._invoke("checkpoint.list", session_id=session_id)
        if not isinstance(result, list) or not result:
            return None
        return RecoverySnapshot.from_dict(result[-1])

    def get_by_run(self, run_id: str) -> RecoverySnapshot | None:
        """Get the latest checkpoint for a run_id."""
        result = self._invoke("checkpoint.latest", run_id=run_id)
        if not isinstance(result, dict) or not result:
            return None
        return RecoverySnapshot.from_dict(result)

    def get_by_id(self, checkpoint_id: str) -> RecoverySnapshot | None:
        """Get a checkpoint by its ID."""
        result = self._invoke("checkpoint.get", checkpoint_id=checkpoint_id)
        if not isinstance(result, dict) or not result:
            return None
        return RecoverySnapshot.from_dict(result)

    def latest(self) -> list[RecoverySnapshot]:
        """All current checkpoints, newest first."""
        result = self._invoke("checkpoint.list")
        if not isinstance(result, list):
            return []
        return [RecoverySnapshot.from_dict(r) for r in reversed(result)]

    def validate(self, checkpoint_id: str) -> tuple[bool, str | None]:
        """Validate a checkpoint by ID.

        Returns ``(valid, error_detail)``. If valid, error_detail is None.
        """
        result = self._invoke("checkpoint.validate", checkpoint_id=checkpoint_id)
        if not isinstance(result, dict):
            return False, "invalid validation response"
        valid = bool(result.get("valid", False))
        detail = result.get("detail")
        return valid, detail

    def close(self) -> None:
        """Release the SQLite handle for this checkpoint store in the Rust cache."""
        self._invoke("checkpoint.close")
        if self._client is not None:
            self._client.close()
            self._client = None