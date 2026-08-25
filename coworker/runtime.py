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

from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable

from .engine import Event, TurnEngine


@runtime_checkable
class RuntimePort(Protocol):
    """The verbs the application layer may drive, and nothing else."""

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
        user_input: "str | list",
        *,
        source: Optional[dict[str, Any]] = None,
        display: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        """Execute one turn for fresh user input; yields the event stream."""
        ...

    def resume(self) -> AsyncIterator[Event]:
        """Resume a durably suspended turn (pending tool calls survive a restart)."""
        ...

    def retry(self) -> AsyncIterator[Event]:
        """Re-run the failed turn without appending a new user message."""
        ...

    def steer(self, text: str, source: Optional[dict[str, Any]] = None) -> None:
        """Deliver an out-of-band instruction into the live turn's next safe boundary."""
        ...

    def interrupt(self) -> None:
        """Stop the turn as soon as possible, from any state."""
        ...


class TurnEngineAdapter:
    """First RuntimePort implementation: wraps the existing TurnEngine unchanged.

    Deliberately thin — delegation only, zero behavior change. Call sites migrate
    to the port surface one by one; `.engine` remains as a marked escape hatch for
    the not-yet-migrated corners (permissions grooming, snapshot persistence), to
    be shrunk over time rather than forced.

    When a RunEventLedger is provided, every driven turn becomes a durable run:
    run.started on entry, run.completed on normal exhaustion, run.failed (then
    re-raised) on error — docs/run-ledger-adr.md.
    """

    def __init__(
        self,
        engine: TurnEngine,
        *,
        ledger: Any = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._session_id = session_id

    @property
    def engine(self) -> TurnEngine:
        """Migration escape hatch — do NOT use for new application-layer code."""
        return self._engine

    # -- RuntimePort -------------------------------------------------------------

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
        user_input: "str | list",
        *,
        source: Optional[dict[str, Any]] = None,
        display: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        return self._track(self._engine.run(user_input, source=source, display=display), "run")

    def resume(self) -> AsyncIterator[Event]:
        return self._track(self._engine.resume(), "resume")

    def retry(self) -> AsyncIterator[Event]:
        return self._track(self._engine.retry(), "retry")

    async def _track(self, agen: AsyncIterator[Event], kind: str) -> AsyncIterator[Event]:
        """Emit the run's durable bookkeeping around the engine's event stream."""
        if self._ledger is None:
            async for event in agen:
                yield event
            return
        import uuid

        run_id = uuid.uuid4().hex
        self._ledger.append(
            run_id,
            "run.started",
            actor="user" if kind == "run" else "system",
            payload={"kind": kind, **({"session_id": self._session_id} if self._session_id else {})},
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

    def steer(self, text: str, source: Optional[dict[str, Any]] = None) -> None:
        self._engine.queue_steering(text, source)

    def interrupt(self) -> None:
        self._engine.request_interrupt()
