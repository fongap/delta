"""RuntimePort — the narrow boundary between the Delta application layer and any
agent runtime.

Delta (the application layer) owns sessions, approvals, sources, artifacts,
automation, audit, and settings. A runtime owns only the intelligence loop:
context assembly, model invocation, tool selection/consumption, compaction,
and the step loop. This module pins that division behind a protocol so the
TurnEngine (OpenWorker lineage) can be replaced or wrapped without the
application layer noticing.

What a Runtime MAY do:
    context assembly · model invocation · tool selection & consumption ·
    compaction · step loop · interrupt · steer · resume

What a Runtime is never given:
    user accounts · provider config · source/artifact lifecycle ·
    approval policy · audit policy · automation · secrets

Human decisions enter the runtime ONLY through the callbacks bound via
`bind()` — never through prompt text or model-visible state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

from core.engine import Event, TurnEngine
from core.permissions import Mode
from core.roots import RootDir


@runtime_checkable
class RuntimePort(Protocol):
    """The verbs the application layer may drive, and nothing else.

    Projections (read-only views) and Commands (mutations) let the application
    layer observe and steer a runtime without ever importing TurnEngine: mode
    changes are `set_mode`, grants are `grant_tool`/`add_task_rule`, thread
    edits are `truncate_messages`, root changes are `upsert_root`/`remove_root`.
    """

    # -- turn driving -------------------------------------------------------------

    def bind(
        self,
        *,
        approver: Any = None,
        directory_requester: Any = None,
        plan_approver: Any = None,
        question_asker: Any = None,
    ) -> None:
        """Attach the application-layer decision callbacks. Approval/question/plan/
        directory answers reach the runtime exclusively through these."""
        ...

    def run(
        self,
        user_input: str | list,
        *,
        source: dict[str, Any] | None = None,
        display: str | None = None,
    ) -> AsyncIterator[Event]:
        """Execute one turn for fresh user input; yields the event stream."""
        ...

    def resume(self) -> AsyncIterator[Event]:
        """Resume a durably suspended turn (pending tool calls survive a restart)."""
        ...

    def retry(self) -> AsyncIterator[Event]:
        """Re-run the failed turn without appending a new user message."""
        ...

    def steer(self, text: str, source: dict[str, Any] | None = None) -> None:
        """Deliver an out-of-band instruction into the live turn's next safe boundary."""
        ...

    def interrupt(self) -> None:
        """Stop the turn as soon as possible, from any state."""
        ...

    # -- projections (read-only state views) ---------------------------------------

    @property
    def agent_name(self) -> str:
        """The agent persona this runtime runs ("code", "cowork", …)."""
        ...

    @property
    def model(self) -> str:
        """The currently selected model id."""
        ...

    @property
    def mode(self) -> Mode:
        """The live permission mode."""
        ...

    @property
    def messages(self) -> list[dict[str, Any]]:
        """The live conversation thread (canonical provider shape). Read-only view;
         edits go through `truncate_messages`."""
        ...

    @property
    def reasoning_effort(self) -> str:
        """The session's reasoning-effort setting ("auto" when unset)."""
        ...

    @property
    def workspace_path(self) -> str:
        """The audit-declared workspace path ("" when the runtime has none)."""
        ...

    @property
    def workspace_dir(self) -> str | None:
        """The executor's working directory, when the agent has one."""
        ...

    @property
    def roots_supported(self) -> bool:
        """Whether this runtime carries a multi-root directory list at all."""
        ...

    def list_roots(self) -> list[RootDir]:
        """Snapshot of the trusted directory roots (primary scratch first)."""
        ...

    def session_grants(self) -> dict[str, Any]:
        """Persistable snapshot of session-scoped "Always allow" approvals."""
        ...

    def compaction_dict(self) -> dict[str, Any]:
        """Serialized auto-compaction view boundary ({} when none)."""
        ...

    # -- commands -------------------------------------------------------------------

    def switch_model(self, model: str) -> str | None:
        """Rebind the session's model; returns the persisted notice or None."""
        ...

    def set_mode(self, value: str) -> None:
        """Switch the permission mode by value; raises ValueError on a bad name."""
        ...

    def set_attended_resolver(self, resolver: Callable[[], bool]) -> None:
        """Live gate for attended-vs-unattended behavior (compaction prompts)."""
        ...

    def grant_tool(self, tool: str) -> None:
        """Add a session-scoped "always allow this tool" approval."""
        ...

    def grant_command(self, command: str) -> None:
        """Add a session-scoped "always allow this command" approval."""
        ...

    def set_allowed_commands(self, commands: list[str]) -> None:
        """Replace the workspace-trust-derived allowed-command list."""
        ...

    def add_task_rule(self, tool: str, target: str) -> None:
        """Grant one target-bound standing rule (tool → declared target)."""
        ...

    def set_task_rules(self, rules: dict[str, set[str]]) -> None:
        """Replace the target-bound standing-rule table wholesale."""
        ...

    def set_reasoning_effort(self, effort: str) -> None:
        """Apply the reasoning-effort setting to every subsequent provider call."""
        ...

    def set_compaction_state(self, state: Any) -> None:
        """Restore a persisted compaction view boundary."""
        ...

    def set_compaction_settings(self, getter: Callable[[], dict[str, Any]]) -> None:
        """Wire the live compaction-settings getter."""
        ...

    def truncate_messages(self, index: int) -> None:
        """Drop the thread from `index` onward (revert flow)."""
        ...

    def upsert_root(self, path: str | Path, writable: bool) -> None:
        """Add a trusted root, or update its access level if already present."""
        ...

    def remove_root(self, path: str | Path) -> None:
        """Remove a non-primary trusted root."""
        ...

    def shutdown_executor(self) -> None:
        """Kill managed background work (app/session teardown); never raises."""
        ...


class TurnEngineAdapter:
    """First RuntimePort implementation: wraps the existing TurnEngine unchanged.

    Deliberately thin — delegation only, zero behavior change. The application
    layer drives every projection/command below; `.engine` remains solely as a
    marked escape hatch for tests and not-yet-migrated corners, to be shrunk
    over time rather than forced.

    When a RunEventLedger is provided, every driven turn becomes a durable run:
    run.started on entry, run.completed on normal exhaustion, run.failed (then
    re-raised) on error — docs/architecture/adr/ADR-001-run-event-ledger.md.
    """

    def __init__(
        self,
        engine: TurnEngine,
        *,
        ledger: Any = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._session_id = session_id
        # ADR-005 (Reliable Task Runtime): an automation run supplies its own
        # durable run id so the TaskRun row, the ledger narrative, the artifact
        # records and the idempotency log all share ONE identity. Interactive
        # turns leave this None and mint a fresh uuid per driven turn.
        self._run_id = run_id
        # Resume path: keep the same run_id across the suspension so the
        # idempotency log (keyed on run_id) sees a replay, not a fresh call.
        # Set lazily on the first run, reused on every resume of the same
        # session.
        self._last_run_id: str | None = run_id

    @property
    def engine(self) -> TurnEngine:
        """Migration escape hatch — tests/debug only. Application-layer code must
        use the port surface; new uses of this property are a review failure."""
        return self._engine

    # -- RuntimePort: turn driving -------------------------------------------------

    def bind(
        self,
        *,
        approver: Any = None,
        directory_requester: Any = None,
        plan_approver: Any = None,
        question_asker: Any = None,
    ) -> None:
        if approver is not None:
            self._engine.approver = approver
        if directory_requester is not None:
            self._engine.directory_requester = directory_requester
        if plan_approver is not None:
            self._engine.plan_approver = plan_approver
        if question_asker is not None:
            self._engine.question_asker = question_asker

    def run(
        self,
        user_input: str | list,
        *,
        source: dict[str, Any] | None = None,
        display: str | None = None,
    ) -> AsyncIterator[Event]:
        return self._track(self._engine.run(user_input, source=source, display=display), "run")

    def resume(self) -> AsyncIterator[Event]:
        return self._track(self._engine.resume(), "resume")

    def retry(self) -> AsyncIterator[Event]:
        return self._track(self._engine.retry(), "retry")

    async def _track(self, agen: AsyncIterator[Event], kind: str) -> AsyncIterator[Event]:
        """Emit the run's durable bookkeeping around the engine's event stream, and
        publish the run identity into the ambient scope so tools and the executor
        (which run below this adapter) can attribute side effects — e.g. background
        process spawn/kill — to the run that caused them."""
        if self._ledger is None:
            async for event in agen:
                yield event
            return
        import uuid

        from core import runscope

        # One identity across the run: an automation supplies run_id at build so
        # the ledger narrative joins the TaskRun row; an interactive turn mints
        # a fresh id per driven turn. A resume of the same session reuses the
        # last id so the idempotency log (keyed on run_id) sees a replay
        # instead of a brand-new call.
        if self._run_id:
            run_id = self._run_id
        elif kind == "resume" and self._last_run_id:
            run_id = self._last_run_id
        else:
            run_id = uuid.uuid4().hex
        self._last_run_id = run_id
        token = runscope.set_current(run_id, self._session_id or "")
        # ADR-007 §10.6 path: persist the audit-declared workspace on
        # the run.started row so P3 Run Analyzer and any future
        # per-workspace query can scope without re-deriving it from
        # payload. Empty string → NULL on disk (handled in _as_dict).
        ws = self.workspace_path or None
        try:
            self._ledger.append(
                run_id,
                "run.started",
                actor="user" if kind == "run" else "system",
                payload={"kind": kind, **({"session_id": self._session_id} if self._session_id else {})},
                workspace=ws,
            )
            try:
                async for event in agen:
                    yield event
            except Exception as exc:
                self._ledger.append(
                    run_id,
                    "run.failed",
                    actor="system",
                    payload={"reason": str(exc), "kind": kind},
                )
                raise
            self._ledger.append(run_id, "run.completed", payload={"kind": kind})
        finally:
            runscope.reset(token)

    def steer(self, text: str, source: dict[str, Any] | None = None) -> None:
        self._engine.queue_steering(text, source)

    def interrupt(self) -> None:
        self._engine.request_interrupt()

    # -- RuntimePort: projections ---------------------------------------------------

    @property
    def agent_name(self) -> str:
        return getattr(self._engine, "agent_name", "code")

    @property
    def model(self) -> str:
        return getattr(self._engine, "model", "")

    @property
    def mode(self) -> Mode:
        permissions = getattr(self._engine, "permissions", None)
        mode = getattr(permissions, "mode", Mode.INTERACTIVE)
        return mode if isinstance(mode, Mode) else Mode.INTERACTIVE

    @property
    def messages(self) -> list[dict[str, Any]]:
        return getattr(self._engine, "messages", [])

    @property
    def reasoning_effort(self) -> str:
        settings = getattr(self._engine, "model_settings", None) or {}
        return settings.get("reasoning_effort") or "auto"

    @property
    def workspace_path(self) -> str:
        context = getattr(self._engine, "audit_context", {}) or {}
        return str(context.get("workspace", ""))

    @property
    def workspace_dir(self) -> str | None:
        executor = getattr(self._engine, "executor", None)
        return str(executor.cwd) if executor is not None else None

    @property
    def roots_supported(self) -> bool:
        return getattr(self._engine, "roots", None) is not None

    def list_roots(self) -> list[RootDir]:
        return list(getattr(self._engine, "roots", None) or [])

    def session_grants(self) -> dict[str, Any]:
        permissions = self._engine.permissions
        tools = sorted(getattr(permissions, "session_allow_tools", None) or ())
        commands = sorted(
            getattr(permissions, "session_allow_commands", None) or ()
        )
        return {"tools": tools, "commands": commands} if (tools or commands) else {}

    def compaction_dict(self) -> dict[str, Any]:
        state = getattr(self._engine, "compaction_state", None)
        return state.as_dict() if state else {}

    # -- RuntimePort: commands --------------------------------------------------------

    def switch_model(self, model: str) -> str | None:
        return self._engine.switch_model(model)

    def set_mode(self, value: str) -> None:
        self._engine.permissions.mode = Mode(value)

    def set_attended_resolver(self, resolver: Callable[[], bool]) -> None:
        self._engine.is_attended = resolver

    def grant_tool(self, tool: str) -> None:
        self._engine.permissions.allow_tool_for_session(str(tool))

    def grant_command(self, command: str) -> None:
        self._engine.permissions.allow_command_for_session(str(command))

    def set_allowed_commands(self, commands: list[str]) -> None:
        self._engine.permissions.allowed_commands = list(commands)

    def add_task_rule(self, tool: str, target: str) -> None:
        rules = self._engine.permissions.task_rules.setdefault(tool, set())
        rules.add(target)

    def set_task_rules(self, rules: dict[str, set[str]]) -> None:
        self._engine.permissions.task_rules = rules

    def set_reasoning_effort(self, effort: str) -> None:
        settings = self._engine.model_settings
        if effort == "auto":
            settings.pop("reasoning_effort", None)
        else:
            settings["reasoning_effort"] = effort

    def set_compaction_state(self, state: Any) -> None:
        self._engine.compaction_state = state

    def set_compaction_settings(self, getter: Callable[[], dict[str, Any]]) -> None:
        self._engine.compaction_settings = getter

    def truncate_messages(self, index: int) -> None:
        self._engine.messages = self._engine.messages[:index]

    def upsert_root(self, path: str | Path, writable: bool) -> None:
        resolved = Path(path)
        roots = getattr(self._engine, "roots", None)
        if roots is None:
            return
        for root in roots:
            if root.path == resolved:
                root.writable = bool(writable)
                return
        roots.append(RootDir(path=resolved, writable=bool(writable)))

    def remove_root(self, path: str | Path) -> None:
        resolved = Path(path)
        roots = getattr(self._engine, "roots", None)
        if roots is None:
            return
        roots[:] = [r for r in roots if r.path != resolved]

    def shutdown_executor(self) -> None:
        executor = getattr(self._engine, "executor", None)
        if executor is not None:
            try:
                executor.shutdown()
            except Exception:
                pass
