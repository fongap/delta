"""Tool Lifecycle Orchestrator (ADR-034 R3 Phase 1).

Extracts tool-call lifecycle orchestration from ``core/engine.py`` into a
dedicated module so that the engine's async streaming loop is separable
from the tool-call authorization / execution / resume flow.

The orchestrator owns:
- **Authorization phase**: gateway classify → permission engine → gateway
  evaluate_policy → approval/plan/directory/ask_user interactive paths.
- **Execution phase**: the execution disposition (execute vs replay vs
  uncertain) is decided by Rust ``toollifecycle.plan`` (ADR-035), which also
  performs the record_planned + mark_executing state-machine transitions.
  Python runs the tool (a capability) and reports the outcome via the
  existing Rust ``idem.commit`` / ``idem.mark_failed`` authority.
- **Resume orchestration**: reconstruct unanswered trailing tool calls and
  re-run them through the orchestrator.
- **Cancellation integration**: accept a ``CancellationToken`` (a Python
  ``asyncio.Event`` wrapper — ADR-037 audited this as a runtime signal with
  no persisted authority value, so it stays in Python).

The ``TurnEngine`` remains the streaming-loop owner and becomes a thin wrapper
over ``ToolLifecycleOrchestrator.authorize_and_execute()`` and ``resume()``.
"""

from __future__ import annotations

import asyncio
import json
import concurrent.futures
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable

from core import gateway
from core.events import Event, EventType
from core.permissions import Mode, PermissionEngine, standing_rule_candidate
from core.risk import WRITE_TOOLS as _WRITE_TOOLS
from integrations.tools import ToolRegistry
from providers import ToolCall

from core.engine_format import (
    _preview,
    _tool_error_message,
    _tool_result_message,
)


class ApprovalOutcome(str, Enum):
    ONCE = "once"
    ALWAYS_TOOL = "always_tool"
    ALWAYS_COMMAND = "always_command"
    DENY = "deny"


@dataclass
class PermissionRequest:
    tool_name: str
    arguments: dict[str, Any]
    metadata: Any
    reason: str
    tool_call_id: str | None = None


Approver = Callable[[PermissionRequest], Awaitable[ApprovalOutcome]]


async def _deny_all(_request: PermissionRequest) -> ApprovalOutcome:
    return ApprovalOutcome.DENY


class CancellationToken:
    """Cooperative cancellation flag (ADR-034).

    Wraps ``asyncio.Event`` — the **signal transport**. The **lifecycle
    decision** (Uncertain vs Failed) on cancel is Rust-authoritative via
    ``toollifecycle.cancel`` (ADR-037 revised). This class sets the flag;
    Rust decides the state-machine consequence.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    @property
    def _raw_event(self) -> asyncio.Event:
        return self._event


@dataclass
class ToolLifecycleContext:
    """Everything the tool lifecycle needs, injected once by the engine.

    Mutable containers (``messages``, ``standing_notes``, ``tool_levels``,
    ``audit_context``) are passed by reference so the orchestrator and the
    engine share the same state.
    """

    registry: ToolRegistry
    permissions: PermissionEngine
    idem_log: Any | None
    ledger: Any | None
    audit_sink: Callable[[dict[str, Any]], None] | None
    audit_context: dict[str, Any]
    cancel_token: CancellationToken
    interrupt_hooks: list[Callable[[], None]]
    # Interactive-tool callbacks
    approver: Approver
    plan_approver: (
        Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]] | None
    )
    directory_requester: (
        Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]] | None
    )
    question_asker: (
        Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]] | None
    )
    # Mutable state (owned by engine, passed by reference)
    messages: list[dict[str, Any]]
    standing_notes: dict[str, str]
    tool_levels: dict[str, Any]
    # Per-tool execution timeout in seconds (ADR-038). <=0 disables.
    tool_timeout: float = 0.0


class ToolLifecycleOrchestrator:
    """Encapsulates the tool-call lifecycle: authorize → execute → record.

    Extracted from ``TurnEngine`` in ADR-034 so that the engine's async
    streaming loop is separable from tool-call orchestration. The execution
    disposition is Rust-authoritative via ``toollifecycle.plan`` (ADR-035);
    the rest (authorization, tool execution dispatch, resume reconstruction,
    cancellation) stays in Python as capability / runtime-guard logic
    (ADR-036/037).
    """

    def __init__(self, ctx: ToolLifecycleContext) -> None:
        self.ctx = ctx

    # -- public API --------------------------------------------------------

    async def authorize_and_execute(
        self, tool_calls: list[ToolCall]
    ) -> AsyncIterator[Event]:
        """Run one assistant turn's tool calls: authorize all of them first
        (sequentially — approval prompts are interactive), then execute.
        Low-risk calls (reads, searches) run concurrently; everything else
        runs one at a time in call order."""
        ctx = self.ctx
        cleared: list[ToolCall] = []
        for tool_call in tool_calls:
            if ctx.cancel_token.is_cancelled():
                yield self._interrupted_tool(tool_call)
                continue
            if isinstance(tool_call.arguments, dict) and set(
                tool_call.arguments.keys()
            ) == {"_raw"}:
                raw_marker = tool_call.arguments.get("_raw")
                detail = (
                    f"tool {tool_call.name!r} returned malformed arguments "
                    f"({len(raw_marker) if isinstance(raw_marker, str) else 'non-JSON'} chars); "
                    "the call was not executed. The model should retry with valid JSON."
                )
                self._audit(tool_call, stage="denied")
                ctx.messages.append(_tool_error_message(tool_call, detail))
                yield Event(
                    EventType.TOOL_FINISHED,
                    {
                        "name": tool_call.name,
                        "status": "error",
                        "reason": detail,
                        "error_type": "UnparsedToolCall",
                    },
                )
                continue
            yield Event(
                EventType.TOOL_PROPOSED,
                {"name": tool_call.name, "arguments": tool_call.arguments},
            )
            spec = ctx.registry.get(tool_call.name)
            ctx.tool_levels[tool_call.id] = gateway.classify(
                tool_call.name, tool_call.arguments, spec.metadata if spec else None
            )
            self._audit(tool_call, stage="proposed")
            if tool_call.name == "request_directory":
                async for event in self._handle_directory_request(tool_call):
                    yield event
                continue
            if tool_call.name == "propose_plan":
                async for event in self._handle_plan_proposal(tool_call):
                    yield event
                continue
            if tool_call.name == "ask_user":
                async for event in self._handle_ask_user(tool_call):
                    yield event
                continue
            allowed = False
            async for item in self._authorize(tool_call):
                if isinstance(item, Event):
                    yield item
                else:
                    allowed = item
            if allowed:
                cleared.append(tool_call)

        concurrent = (
            [tc for tc in cleared if self._parallel_safe(tc)]
            if len(cleared) > 1
            else []
        )
        serial = [tc for tc in cleared if tc not in concurrent]

        if concurrent:
            for tool_call in concurrent:
                yield Event(EventType.TOOL_STARTED, {"name": tool_call.name})
                self._audit(tool_call, stage="started")
            outcomes = await asyncio.gather(
                *[asyncio.to_thread(self._execute_sync, tc) for tc in concurrent]
            )
            for tool_call, (result, status) in zip(concurrent, outcomes):
                yield self._record_result(tool_call, result, status)

        for tool_call in serial:
            if ctx.cancel_token.is_cancelled():
                yield self._interrupted_tool(tool_call)
                continue
            yield Event(EventType.TOOL_STARTED, {"name": tool_call.name})
            self._audit(tool_call, stage="started")
            result, status = await asyncio.to_thread(self._execute_sync, tool_call)
            yield self._record_result(tool_call, result, status)

    async def resume(self, pending_calls: list[ToolCall]) -> AsyncIterator[Event]:
        """Re-process pending tool calls from a suspended turn (durable resume).

        The idempotency log handles dedup: already-committed calls are skipped
        (replayed), uncertain calls surface for user resolution.
        """
        async for event in self.authorize_and_execute(pending_calls):
            yield event

    def unanswered_trailing_tool_calls(self) -> list[ToolCall]:
        """Reconstruct unanswered trailing tool calls from message history."""
        ctx = self.ctx
        answered = {
            m.get("tool_call_id") for m in ctx.messages if m.get("role") == "tool"
        }
        for msg in reversed(ctx.messages):
            if msg.get("role") == "user":
                return []
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                out: list[ToolCall] = []
                for tc in msg["tool_calls"]:
                    if tc.get("id") in answered:
                        continue
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    out.append(
                        ToolCall(
                            id=str(tc.get("id") or ""),
                            name=str(fn.get("name") or ""),
                            arguments=args,
                        )
                    )
                return out
        return []

    # -- internal: execution -----------------------------------------------

    def _execute_sync(self, tool_call: ToolCall) -> tuple[Any, str]:
        """Execute one authorized call (runs in a worker thread).

        P0-A Side Effect Crash Safety — the execution path moves through the
        state machine Planned → Executing → Committed|Failed. On resume, an
        Uncertain row is NEVER auto-replayed; the orchestrator surfaces it as
        an "uncertain" status so the run can enter the Inbox for user
        resolution.

        ADR-035: the execution disposition (execute vs replay vs uncertain)
        and its paired record_planned + mark_executing transitions are
        decided by Rust ``toollifecycle.plan``. Python only runs the tool
        (a capability, not authority) and reports the outcome.

        ADR-038: when ``tool_timeout`` is configured, the registry call is
        bounded by a deadline. On timeout, Python asks Rust for the lifecycle
        consequence (Executing → Uncertain, Planned → Failed) and returns a
        timeout result. The state-machine decision is Rust-authoritative;
        the deadline mechanism is Python capability.
        """
        ctx = self.ctx
        idem_log = ctx.idem_log
        planned = False
        if idem_log is not None:
            from core import runscope

            scope = runscope.current()
            if scope is not None:
                run_id, _session_id = scope
                if run_id:
                    disposition = idem_log.plan(
                        run_id,
                        tool_call.id,
                        tool_call.name,
                        tool_call.arguments,
                        ledger=ctx.ledger,
                        workspace=ctx.audit_context.get("workspace"),
                    )
                    action = disposition.get("action")
                    if action == "replay":
                        return disposition.get("result"), "replayed"
                    if action == "uncertain":
                        return (
                            disposition.get("result")
                            or {
                                "error": "side effect is uncertain — "
                                "the previous run may or may not have "
                                "executed it. User resolution required.",
                                "operation_id": disposition.get("operation_id", ""),
                            },
                            "uncertain",
                        )
                    # action == "execute": Rust already recorded planned +
                    # marked executing; remember to report the outcome.
                    planned = run_id or ""
        timeout = ctx.tool_timeout
        try:
            if timeout and timeout > 0:
                # AF-08: do NOT use `with` — its __exit__ calls
                # shutdown(wait=True), which blocks until the orphaned
                # thread finishes, defeating the timeout. Instead,
                # submit, wait, and abandon the thread if it times out.
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(
                    ctx.registry.execute, tool_call.name, tool_call.arguments
                )
                try:
                    result = future.result(timeout=timeout)
                except concurrent.futures.TimeoutError:
                    # Abandon the worker thread — it may still be running.
                    # The tool's side effect (if any) is in an uncertain
                    # state: we stopped waiting, but the callable may
                    # complete in the background. Mark as uncertain.
                    pool.shutdown(wait=False)
                    raise
                else:
                    pool.shutdown(wait=True)
            else:
                result = ctx.registry.execute(tool_call.name, tool_call.arguments)
        except concurrent.futures.TimeoutError:
            if planned:
                from core import runscope

                scope = runscope.current()
                if scope is not None:
                    run_id, _session_id = scope
                    if run_id:
                        try:
                            idem_log.cancel(  # type: ignore[union-attr]
                                run_id,
                                tool_call.id,
                                tool_call.name,
                                reason="timeout",
                                ledger=ctx.ledger,
                                workspace=ctx.audit_context.get("workspace"),
                            )
                        except Exception:
                            pass
            # AF-08: timeout means we stopped waiting, not that the
            # thread was killed. If the tool may have side effects,
            # the state is uncertain, not failed.
            return (
                {"error": f"tool timed out after {timeout:.0f}s", "error_type": "TimeoutError"},
                "uncertain",
            )
        except Exception as exc:
            if planned:
                from core import runscope

                scope = runscope.current()
                if scope is not None:
                    run_id, _session_id = scope
                    if run_id:
                        try:
                            idem_log.mark_failed(  # type: ignore[union-attr]
                                run_id,
                                tool_call.id,
                                str(exc),
                                ledger=ctx.ledger,
                            )
                        except Exception:
                            pass
            return {"error": str(exc), "error_type": type(exc).__name__}, "error"
        if planned and result is not None:
            from core import runscope

            scope = runscope.current()
            if scope is not None:
                run_id, _session_id = scope
                if run_id:
                    # AF-09: if the side effect already executed but
                    # trusted persistence fails, we must NOT return
                    # "ok" and continue. The state is uncertain:
                    # the effect happened but was not recorded.
                    try:
                        idem_log.commit(  # type: ignore[union-attr]
                            run_id,
                            tool_call.id,
                            tool_call.name,
                            tool_call.arguments,
                            result,
                            ledger=ctx.ledger,
                        )
                    except Exception:
                        return (
                            {
                                "error": "post_effect_persistence_failed",
                                "error_type": "PersistenceFailure",
                                "tool": tool_call.name,
                            },
                            "uncertain",
                        )
                    if tool_call.name in _WRITE_TOOLS and isinstance(
                        tool_call.arguments, dict
                    ):
                        from core.artifact import register_artifact

                        ws = ctx.audit_context.get("workspace", "")
                        file_path = (
                            tool_call.arguments.get("path")
                            or tool_call.arguments.get("file_path")
                            or ""
                        )
                        if ws and file_path and ctx.ledger is not None:
                            try:
                                register_artifact(
                                    ws,
                                    file_path,
                                    run_id=run_id,
                                    ledger=ctx.ledger,
                                )
                            except Exception:
                                pass
        return result, "ok"

    def _record_result(self, tool_call: ToolCall, result: Any, status: str) -> Event:
        ctx = self.ctx
        display: dict[str, Any] | None = None
        if isinstance(result, dict) and "_display" in result:
            display = result.get("_display") or None
            result = {k: v for k, v in result.items() if k != "_display"}
        message = _tool_result_message(tool_call, result)
        if display:
            message["_display"] = display
        ctx.messages.append(message)
        hidden = int((display or {}).get("hidden_by_filters") or 0)
        stripped = int((display or {}).get("hidden_fields") or 0)
        if hidden or stripped:
            parts = []
            if hidden:
                parts.append(f"{hidden} result(s) hidden")
            if stripped:
                parts.append(f"{stripped} field value(s) stripped")
            self._audit(
                tool_call,
                stage="filtered",
                status="hidden",
                reason=" · ".join(parts) + " by privacy filters",
            )
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        rule = ctx.standing_notes.pop(tool_call.id, "")
        return Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
                **({"display": display} if display else {}),
                **({"standing_rule": rule} if rule else {}),
            },
        )

    # -- internal: authorization -------------------------------------------

    async def _authorize(self, tool_call: ToolCall) -> AsyncIterator[Event | bool]:
        """Permission flow for one call. Yields its events, then True/False
        (allowed) last. Denied/unknown calls get their tool-error message
        appended here."""
        ctx = self.ctx
        spec = ctx.registry.get(tool_call.name)
        metadata = spec.metadata if spec else None

        decision = ctx.permissions.evaluate(
            tool_call.name, tool_call.arguments, metadata
        )
        level = ctx.tool_levels.get(tool_call.id, gateway.RiskLevel.L4)
        decision, level = gateway.evaluate_policy(
            decision,
            level,
            tool_call.name,
            tool_call.arguments,
            metadata,
            workspace_root=ctx.permissions.workspace_root,
            roots=ctx.permissions.resolved_roots(),
        )
        allowed = decision.allowed
        reason = decision.reason

        if allowed and decision.rule:
            ctx.standing_notes[tool_call.id] = decision.rule
            self._audit(
                tool_call, stage="auto_allowed", status="allowed", reason=reason
            )

        if not allowed and decision.needs_user:
            yield Event(
                EventType.PERMISSION_REQUIRED,
                {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "reason": decision.reason,
                    "category": getattr(metadata, "category", ""),
                    "standing_target": standing_rule_candidate(
                        tool_call.name,
                        tool_call.arguments,
                        metadata,
                        ctx.permissions.risk_overrides,
                    ),
                },
            )
            self._audit(tool_call, stage="approval_requested", reason=decision.reason)
            outcome = await self._interruptible(
                ctx.approver(
                    PermissionRequest(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        metadata=metadata,
                        reason=decision.reason,
                        tool_call_id=tool_call.id,
                    )
                ),
                interrupted=ApprovalOutcome.DENY,
            )
            if outcome is ApprovalOutcome.DENY:
                allowed, reason = (
                    False,
                    "interrupted by user"
                    if ctx.cancel_token.is_cancelled()
                    else "denied by user",
                )
                self._audit(
                    tool_call,
                    stage="approval_resolved",
                    status="denied",
                    approval=outcome.value,
                    reason=reason,
                )
            else:
                if level < gateway.RiskLevel.L3:
                    if outcome is ApprovalOutcome.ALWAYS_TOOL:
                        ctx.permissions.allow_tool_for_session(tool_call.name)
                    elif outcome is ApprovalOutcome.ALWAYS_COMMAND:
                        ctx.permissions.allow_command_for_session(
                            str(tool_call.arguments.get("command", ""))
                        )
                allowed, reason = True, "approved by user"
                self._audit(
                    tool_call,
                    stage="approval_resolved",
                    status="approved",
                    approval=outcome.value,
                    reason=reason,
                )

        if not allowed:
            if spec is None:
                reason = f"unknown tool: {tool_call.name}"
            ctx.messages.append(_tool_error_message(tool_call, reason))
            yield Event(
                EventType.TOOL_FINISHED,
                {"name": tool_call.name, "status": "denied", "reason": reason},
            )
            self._audit(tool_call, stage="finished", status="denied", reason=reason)
            yield False
            return

        if spec is None:
            ctx.messages.append(
                _tool_error_message(tool_call, f"unknown tool: {tool_call.name}")
            )
            yield Event(
                EventType.TOOL_FINISHED,
                {"name": tool_call.name, "status": "error", "reason": "unknown tool"},
            )
            yield False
            return

        yield True

    # -- internal: helpers ------------------------------------------------

    def _interrupted_tool(self, tool_call: ToolCall) -> Event:
        """ADR-037: the cancellation lifecycle decision is Rust-authoritative.

        Python asks Rust what state the side effect should be in: Uncertain
        (was Executing, result unknown), Failed (was Planned, never started),
        or Terminal (already settled). The event status reflects Rust's
        decision, never a Python-hardcoded "interrupted".
        """
        return self._interrupt_tool(tool_call, reason="user_stop", audit_reason="user stop")

    def _timed_out_tool(self, tool_call: ToolCall) -> Event:
        """ADR-038: the timeout lifecycle decision is Rust-authoritative.

        Same state-machine decision as cancellation (Executing → Uncertain,
        Planned → Failed), but the audit reason is "timeout" so operators
        can distinguish the two interruption causes.
        """
        return self._interrupt_tool(tool_call, reason="timeout", audit_reason="tool timeout")

    def _interrupt_tool(
        self, tool_call: ToolCall, *, reason: str, audit_reason: str
    ) -> Event:
        """Shared lifecycle-decision path for cancellation and timeout."""
        ctx = self.ctx
        idem_log = ctx.idem_log
        rust_status = "interrupted"
        if idem_log is not None:
            from core import runscope

            scope = runscope.current()
            if scope is not None:
                run_id, _session_id = scope
                if run_id:
                    try:
                        decision = idem_log.cancel(
                            run_id,
                            tool_call.id,
                            tool_call.name,
                            reason=reason,
                            ledger=ctx.ledger,
                            workspace=ctx.audit_context.get("workspace"),
                        )
                        action = decision.get("action")
                        if action == "uncertain":
                            rust_status = "uncertain"
                        elif action == "failed":
                            rust_status = "denied"
                        elif action == "terminal":
                            rust_status = "terminal"
                    except Exception:
                        pass  # best-effort: a dead authority must not block the stop
        ctx.messages.append(
            _tool_error_message(tool_call, f"interrupted ({audit_reason})")
        )
        self._audit(
            tool_call,
            stage="finished",
            status=rust_status,
            reason=audit_reason,
        )
        return Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": rust_status,
                "reason": audit_reason,
            },
        )

    def _parallel_safe(self, tool_call: ToolCall) -> bool:
        spec = self.ctx.registry.get(tool_call.name)
        metadata = spec.metadata if spec else None
        return getattr(metadata, "risk_level", "") == "low" and not getattr(
            metadata, "requires_approval", False
        )

    def _audit(self, tool_call: ToolCall, **event: Any) -> None:
        ctx = self.ctx
        if ctx.audit_sink is None:
            return
        payload = {
            **ctx.audit_context,
            "tool": tool_call.name,
            "arguments": tool_call.arguments,
            "level": getattr(ctx.tool_levels.get(tool_call.id), "name", ""),
            "isolation": gateway.isolation_status(ctx.tool_levels.get(tool_call.id)),
            **event,
        }
        try:
            ctx.audit_sink(payload)
        except Exception:
            pass

    async def _interruptible(self, coro: Any, interrupted: Any) -> Any:
        """Await ``coro``, but resolve early with ``interrupted`` if the user
        stops the turn. The pending task is cancelled so an answered-later
        Inbox card no-ops."""
        task = asyncio.ensure_future(coro)
        cancel_wait = asyncio.ensure_future(self.ctx.cancel_token.wait())
        try:
            done, _ = await asyncio.wait(
                {task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                return task.result()
            task.cancel()
            return interrupted
        finally:
            cancel_wait.cancel()

    # -- internal: interactive tools --------------------------------------

    async def _handle_plan_proposal(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        ctx = self.ctx
        args = tool_call.arguments or {}
        plan = str(args.get("plan", ""))
        if ctx.permissions.mode is not Mode.PLAN:
            if ctx.permissions.mode is Mode.DISCUSS:
                error = (
                    "not in plan mode — this is discuss mode (read-only), so describe "
                    "the proposed changes in chat instead"
                )
            else:
                error = "not in plan mode — proceed with the work directly"
            result: dict[str, Any] = {"approved": False, "error": error}
        elif ctx.plan_approver is None:
            result = {
                "approved": False,
                "error": "plan approval isn't available here",
            }
        else:
            yield Event(EventType.PLAN_PROPOSED, {"plan": plan})
            self._audit(tool_call, stage="plan_proposed")
            result = await self._interruptible(
                ctx.plan_approver(dict(args), tool_call.id),
                interrupted={"approved": False, "error": "interrupted by user"},
            ) or {
                "approved": False,
                "error": "no response",
            }

        if result.get("approved"):
            try:
                ctx.permissions.mode = Mode(str(result.get("mode", "interactive")))
            except ValueError:
                ctx.permissions.mode = Mode.INTERACTIVE
            result = {
                **result,
                "mode": ctx.permissions.mode.value,
                "note": "plan approved — implement it now",
            }

        status = "ok" if result.get("approved") else "denied"
        ctx.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_directory_request(
        self, tool_call: ToolCall
    ) -> AsyncIterator[Event]:
        ctx = self.ctx
        args = tool_call.arguments or {}
        if ctx.directory_requester is None:
            result: dict[str, Any] = {
                "granted": False,
                "error": "directory requests aren't available here",
            }
        else:
            yield Event(
                EventType.DIRECTORY_REQUESTED,
                {
                    "reason": str(args.get("reason", "")),
                    "path": str(args.get("path", "")),
                    "writable": bool(args.get("writable", False)),
                },
            )
            self._audit(
                tool_call,
                stage="directory_requested",
                reason=str(args.get("reason", "")),
            )
            result = await self._interruptible(
                ctx.directory_requester(dict(args), tool_call.id),
                interrupted={"granted": False, "error": "interrupted by user"},
            ) or {
                "granted": False,
                "error": "no response",
            }

        status = "ok" if result.get("granted") else "denied"
        ctx.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_ask_user(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        ctx = self.ctx
        args = tool_call.arguments or {}
        question = str(args.get("question", "")).strip()
        if not question:
            for entry in args.get("questions") or []:
                if isinstance(entry, dict) and str(entry.get("question", "")).strip():
                    question = str(entry["question"]).strip()
                    break
        if ctx.question_asker is None or not question:
            result: dict[str, Any] = {
                "answer": "",
                "error": (
                    "no question was asked"
                    if not question
                    else "asking isn't available here"
                ),
            }
        else:
            self._audit(tool_call, stage="question_requested", reason=question)
            result = await self._interruptible(
                ctx.question_asker(dict(args), tool_call.id),
                interrupted={"answer": "", "error": "interrupted by user"},
            ) or {
                "answer": "",
                "error": "no response",
            }

        status = "ok" if (result.get("answer") or result.get("answers")) else "denied"
        ctx.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )
