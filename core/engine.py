"""TurnEngine �?the owned agent loop.

Async, but with blocking provider/tool calls wrapped in `asyncio.to_thread` so the loop
(and any UI consuming its events) stays responsive. One user turn spans many model↔tool
iterations until the model stops requesting tools, a rail trips, or it's interrupted.
When the model requests several tool calls in one turn, low-risk ones (reads, searches)
execute concurrently; writes/shell stay strictly ordered.

Approvals are handled out-of-band via an injected async `approver`: when the permission
engine says `needs_user`, the engine emits `PERMISSION_REQUIRED` and awaits the approver.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from core import compaction as _compaction
from core.call_errors import (
    ErrorClass,
    TTFTTimeoutError,
    classify_error,
    is_retryable,
    wait_for_retry_async,
)
from core.identity import (
    IDENTITY_CLAUSE,
    answer as identity_answer,
    display_model_name,
    match_identity,
)
from core.events import Event, EventType
from core.permissions import PermissionEngine
from core.tool_lifecycle import (
    ApprovalOutcome,  # noqa: F401  (re-exported for public API compat)
    Approver,
    CancellationToken,
    PermissionRequest,  # noqa: F401  (re-exported for public API compat)
    ToolLifecycleContext,
    ToolLifecycleOrchestrator,
    _deny_all,
)
from providers import AssistantTurn, ProviderClient, ToolCall  # noqa: F401  (re-exported)
from providers.errors import friendly_model_error
from providers.openai_provider import looks_like_unparsed_tool_call
from core import tool_selection as _tool_selection
from integrations.tools import ToolRegistry

# Stateless message-formatting helpers moved to engine_format; the TurnEngine body resolves
# them through these names exactly as it did when they were defined in this module.
from core.engine_format import (
    _assistant_message,
)


class TurnEngine:
    def __init__(
        self,
        *,
        provider: ProviderClient,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        model: str,
        instructions: str | None = None,
        approver: Approver | None = None,
        max_iterations: int = 12,
        model_settings: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
        context_provider: Callable[[], str] | None = None,
        directory_requester: (
            Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]] | None
        ) = None,
        plan_approver: (
            Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]] | None
        ) = None,
        question_asker: (
            Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]] | None
        ) = None,
        # Called (thread-safe, best-effort) when the user stops the turn �?e.g. the
        # executor's kill for a running shell command.
        interrupt_hooks: list[Callable[[], None]] | None = None,
        # Per-call tool injection policy (core/tool_selection.py): "auto" (default)
        # resolves the relevant subset per call; anything else ("full", "off") restores
        # the historical always-everything injection.
        tool_selection: str = "auto",
        # The agent's family ("code" pins the workspace tool base; see FAMILY_BASE).
        agent_family: str = "knowledge",
        # Request observability sink (core/request_log.py): one dict per model call,
        # best-effort. None (tests, subagents) disables recording.
        request_logger: Callable[[dict[str, Any]], None] | None = None,
        # TTFT ceiling (seconds) for the first streamed token �?the pre-first-token wait
        # on free/shared gateways is the timeout killer. None/<=0 disables the guard.
        ttft_timeout: float | None = None,
        # Per-tool execution timeout (seconds, ADR-038). When a tool call
        # exceeds this ceiling, the orchestrator fires the timeout path:
        # Rust decides Executing → Uncertain, Planned → Failed. <=0/None
        # disables the guard.
        tool_timeout: float | None = None,
        # Bounded retry for TRANSIENT provider failures (429/5xx/connection/stall) �?
        # Codex-style exponential backoff. Never retries stream truncation (finish_reason
        # guard) or context overflow (compaction's job). 0/None disables auto-retry.
        max_retries: int = 2,
        # ADR-005 WS4: side-effect idempotency log. When set, every
        # consequential tool call consults the log before executing and
        # commits a row + `side_effect.committed` event after a successful
        # execution. `resume()` then skips calls that already committed
        # (the previous run's side effects are not re-played). None
        # disables the log (tests, read-only subagents).
        idem_log: Any | None = None,
        # P0-B: the run-event ledger, so _execute_sync can pass ledger=
        # to idem_log state-transition calls. Each IdempotencyLog method
        # is the single choke point that writes both the idempotency state
        # AND the corresponding side_effect.* ledger event in one call.
        # None disables ledger events (tests, read-only subagents).
        ledger: Any | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.idem_log = idem_log
        self.ledger = ledger
        self.permissions = permissions
        self.model = model
        self.approver = approver or _deny_all
        self.max_iterations = max_iterations
        self.model_settings = dict(model_settings or {})
        self.messages: list[dict[str, Any]] = list(messages or [])
        self.audit_sink = audit_sink
        # Returns an ephemeral `<system-context>` block appended to the LAST user message at
        # send-time only (never persisted). We can't reliably inject system messages mid-thread
        # across providers, so dynamic per-turn context (e.g. the live directory list) rides on
        # the latest user turn. Returns "" when there's nothing to add.
        self.context_provider = context_provider
        # Handles the `request_directory` tool: emits a DIRECTORY_REQUESTED prompt, waits for the
        # user to grant/decline a folder out-of-band, applies the grant to this live session, and
        # returns the outcome. None on surfaces that can't prompt (the tool then no-ops).
        self.directory_requester = directory_requester
        # Handles the `propose_plan` tool: emits PLAN_PROPOSED, waits for the user's decision.
        # An approving result flips the live PermissionEngine out of plan mode (same session,
        # context kept). None on surfaces that can't prompt (the tool then no-ops).
        self.plan_approver = plan_approver
        # Handles the `ask_user` tool: turns a question into an Inbox item and waits for the answer
        # (answerable inline in a live session or from the Inbox when unattended). None on surfaces
        # that can't ask (the tool then no-ops).
        self.question_asker = question_asker
        # Auto-compaction (OPE-27) �?set post-construction by the surface/manager so the
        # constructor footprint stays put. `compaction_settings` is a live getter (Settings
        # changes apply without a rebuild); `is_attended` gates the failure prompt (None �?
        # treat as unattended: never park a background run on internal bookkeeping).
        self.compaction_state: _compaction.CompactionState | None = None
        self.compaction_settings: Callable[[], dict[str, Any]] | None = None
        self.is_attended: Callable[[], bool] | None = None
        self._last_context_tokens: int | None = None
        self.audit_context: dict[str, Any] = {}
        if instructions and not (
            self.messages and self.messages[0].get("role") == "system"
        ):
            self.messages.insert(
                0,
                {
                    "role": "system",
                    "content": instructions + "\n\n" + IDENTITY_CLAUSE,
                },
            )
        self._cancel = CancellationToken()
        # Each pending steering message: (text, optional MessageSource sidecar dict).
        self._steering: list[tuple[str, dict[str, Any] | None]] = []
        # tool_call.id �?the standing rule that auto-allowed it ("tool �?target"), so the
        # TOOL_FINISHED event can carry the note to the tool card (§25).
        self._standing_notes: dict[str, str] = {}
        # Execution Gateway: tool_call.id �?RiskLevel, stamped at proposal time and
        # carried on every audit row for that call (see core/gateway.py).
        self._tool_levels: dict[str, Any] = {}
        self._interrupt_hooks: list[Callable[[], None]] = list(interrupt_hooks or [])
        # Tool Lifecycle Orchestrator (ADR-034): extracted from TurnEngine so the
        # async streaming loop is separable from tool-call orchestration.
        self._tool_lifecycle = ToolLifecycleOrchestrator(
            ToolLifecycleContext(
                registry=registry,
                permissions=permissions,
                idem_log=idem_log,
                ledger=ledger,
                audit_sink=audit_sink,
                audit_context=self.audit_context,
                cancel_token=self._cancel,
                interrupt_hooks=self._interrupt_hooks,
                approver=self.approver,
                plan_approver=plan_approver,
                directory_requester=directory_requester,
                question_asker=question_asker,
                messages=self.messages,
                standing_notes=self._standing_notes,
                tool_levels=self._tool_levels,
                tool_timeout=(
                    float(tool_timeout)
                    if tool_timeout is not None and tool_timeout > 0
                    else 0.0
                ),
            )
        )
        # Tool injection state (v0.3.0 P0): `_tool_expanded` is the one-way escape hatch
        # �?the model visibly reached for a tool it couldn't see, so the session falls
        # back to full injection for good. `_tools_minimal` is the context-budget trim
        # (core set only for this stretch of history); it resets when compaction frees
        # room.
        self.tool_selection = (
            tool_selection if str(tool_selection).lower() == "auto" else "full"
        )
        self.agent_family = agent_family
        self.request_logger = request_logger
        self.ttft_timeout = (
            float(ttft_timeout) if ttft_timeout is not None and ttft_timeout > 0 else None
        )
        self.max_retries = max(0, int(max_retries or 0))
        self._tool_expanded = False
        self._tools_minimal = False
        # Transient-retry budget, consumed across the WHOLE turn (all iterations):
        # a persistent stall must give up after max_retries total, not per iteration.
        self._turn_retries = 0

    # -- external controls ------------------------------------------------------
    def request_interrupt(self) -> None:
        """Stop the turn as soon as possible, from ANY state: mid-stream (the producer
        thread drops the stream between chunks), mid-tool (interrupt hooks kill the
        running command), awaiting an approval/question/plan (the await resolves as
        interrupted), or between iterations (the loop checkpoint). Every pending
        tool_call still gets a tool-error result so the history never carries orphans
        (hosted templates reject them, and durable-resume would re-prompt them)."""
        self._cancel.cancel()
        for hook in self._interrupt_hooks:
            try:
                hook()
            except Exception:
                pass  # best-effort: a dead executor must not block the stop

    async def _interruptible(self, coro: Any, interrupted: Any) -> Any:
        """Await ``coro``, but resolve early with ``interrupted`` if the user stops the
        turn. Delegates to the Tool Lifecycle Orchestrator (ADR-034)."""
        return await self._tool_lifecycle._interruptible(coro, interrupted)

    def queue_steering(
        self, text: str, source: dict[str, Any] | None = None
    ) -> None:
        self._steering.append((text, source))

    # -- main loop --------------------------------------------------------------
    async def run(
        self,
        user_input: str | list,
        *,
        source: dict[str, Any] | None = None,
        display: str | None = None,
    ) -> AsyncIterator[Event]:
        # `user_input` is a string, or OpenAI content-parts (text + image_url) for attachments.
        # `source` (a MessageSource dict) is a display-only sidecar for connector messages: it
        # rides on the persisted user message + the TURN_START event, but is stripped before the
        # message reaches a provider (see `_outbound_messages`). `content` stays the framed text.
        # `display` is the same split for force-run skills (SKILLS-SPEC §4.1 #3): the user's
        # literal "/skill �? line for the transcript, while `content` carries the model-facing
        # framing. `ts` (unix seconds, stamped on every appended message) is the same kind of
        # sidecar.
        # Identity-class questions ("你是�? / "who are you" / "什么模型驱动你" / �? are answered
        # locally without a model call, so the underlying LLM never claims a foreign product
        # identity. Only a plain string that is *entirely* an identity question trips this
        # (match_identity is whole-message anchored), so attachments and normal questions fall
        # through to the model untouched.
        if isinstance(user_input, str):
            kind = match_identity(user_input)
            if kind:
                async for event in self._run_identity(
                    user_input, kind, source=source, display=display
                ):
                    yield event
                return
        message: dict[str, Any] = {
            "role": "user",
            "content": user_input,
            "ts": time.time(),
        }
        if source is not None:
            message["source"] = source
        if display is not None:
            message["_display"] = display
        self.messages.append(message)
        self._cancel.clear()
        self._turn_retries = 0
        data: dict[str, Any] = {"input": user_input}
        if source is not None:
            data["source"] = source
        if display is not None:
            data["display"] = display
        yield Event(EventType.TURN_START, data)
        async for event in self._loop():
            yield event

    async def _run_identity(
        self,
        user_input: str,
        kind: str,
        *,
        source: dict[str, Any] | None = None,
        display: str | None = None,
    ) -> AsyncIterator[Event]:
        """Answer an identity question locally (no model call). Mirrors a streamed text
        turn's event sequence (TURN_START �?ASSISTANT_DELTA �?ASSISTANT_MESSAGE �?
        TURN_END) and persists both the user's question and the answer, so the
        transcript and history stay consistent. The model name comes from the live
        ``self.model`` (the actual model that would have driven the turn), never guessed.
        """
        message: dict[str, Any] = {
            "role": "user",
            "content": user_input,
            "ts": time.time(),
        }
        if source is not None:
            message["source"] = source
        if display is not None:
            message["_display"] = display
        self.messages.append(message)
        self._cancel.clear()
        data: dict[str, Any] = {"input": user_input}
        if source is not None:
            data["source"] = source
        if display is not None:
            data["display"] = display
        yield Event(EventType.TURN_START, data)
        text = identity_answer(kind, display_model_name(self.model))
        self.messages.append(_assistant_message(AssistantTurn(text=text), model=self.model))
        yield Event(EventType.ASSISTANT_DELTA, {"text": text})
        yield Event(EventType.ASSISTANT_MESSAGE, {"text": text, "tool_calls": []})
        yield Event(EventType.TURN_END, {"status": "completed", "iterations": 0})

    def switch_model(self, model: str) -> str | None:
        """Rebind the session's model mid-conversation (roadmap item 3). History is
        canonical OpenAI shape and every provider converts per call, so the switch is just
        the field write �?plus a persisted notice marking WHERE it happened, with a
        degradation warning when history carries images the new model can't see (those are
        sent as placeholders �?see `_outbound_messages`). Returns the notice text, or None
        when nothing changed (same model, or first bind on a fresh session)."""
        if not model or model == self.model:
            return None
        had_history = any(m.get("role") != "system" for m in self.messages)
        self.model = model
        if not had_history:
            return None
        text = f"Model switched to {display_model_name(model)}"
        try:
            caps = self.provider.capabilities(model)
        except Exception:
            caps = None
        image_warning = False
        if (
            caps is not None
            and not getattr(caps, "vision", False)
            and self._history_has_images()
        ):
            text += " �?earlier images can't be read by this model"
            image_warning = True
        self._append_notice("model_switch", text, model=model, image_warning=image_warning)
        return text

    def _history_has_images(self) -> bool:
        return any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for msg in self.messages
            if isinstance(msg.get("content"), list)
            for p in msg["content"]
        )

    def _tail_is_retriable_error(self) -> bool:
        """True when the history tail is an error notice, looking through any model_switch
        notices appended after it (a switch must not consume the retry)."""
        for message in reversed(self.messages):
            if message.get("role") != "notice":
                return False
            if message.get("kind") == "model_switch":
                continue
            return message.get("kind") == "error"
        return False

    def _append_notice(self, kind: str, text: str | None = None, **extra: Any) -> None:
        """Persist a turn-ending marker (error/interrupted) as a display-only `notice`
        message: it survives reload like the transcript does, but `_outbound_messages`
        drops the role so no provider ever sees it. Extra fields (e.g. ``model`` for
        ``model_switch``) let the frontend localize the display text."""
        notice: dict[str, Any] = {"role": "notice", "kind": kind, "ts": time.time()}
        if text:
            notice["text"] = text
        notice.update(extra)
        self.messages.append(notice)

    async def retry(self) -> AsyncIterator[Event]:
        """Re-run the model loop after a provider error �?no new user message; the failed
        turn's input is already the tail of history. Guarded on the tail being an error
        notice so a stray retry frame can't re-answer a completed turn. Trailing
        model_switch notices don't break the guard �?switching models and THEN retrying
        is the intended recovery path (owner-hit 2026-07-23)."""
        if not self._tail_is_retriable_error():
            return
        self._cancel.clear()
        self._turn_retries = 0
        yield Event(EventType.TURN_START, {"input": ""})
        async for event in self._loop():
            yield event

    async def resume(self) -> AsyncIterator[Event]:
        """Continue a turn that was suspended at a prompt and persisted �?durable resume after a
        restart (or engine eviction). Re-process the trailing assistant message's UNANSWERED
        tool-calls (the prompt callbacks find the already-resolved Inbox item and return without
        re-prompting; answered calls are skipped, so nothing double-executes), then run the model
        loop to finish the turn."""
        pending = self._tool_lifecycle.unanswered_trailing_tool_calls()
        if not pending:
            return
        self._cancel.clear()
        self._turn_retries = 0
        yield Event(EventType.TURN_START, {"input": "(resumed)"})
        async for event in self._tool_lifecycle.authorize_and_execute(pending):
            yield event
        yield Event(EventType.ITERATION_END, {"iteration": 0})
        if not self._cancel.is_cancelled():
            async for event in self._loop():
                yield event

    async def _loop(self) -> AsyncIterator[Event]:
        iterations = 0
        while True:
            if iterations >= self.max_iterations:
                yield Event(
                    EventType.TURN_END,
                    {"status": "max_iterations_exceeded", "iterations": iterations},
                )
                return
            iterations += 1

            # Auto-compaction checkpoint (OPE-27): between tool turns and before a new
            # turn's first call. Deliberately no "wrap up" warning to the model. The
            # COMPACTING signal precedes the (multi-second) summarizer call so surfaces
            # can show progress instead of a silent stall. Budget order (v0.3.0 P0):
            # tool schemas count toward the trigger, and trimming the toolset is tried
            # BEFORE the summarizer �?the cheapest payload goes first.
            turn_tools, turn_tool_names, turn_tool_mode = self._tools_for_call()
            notice = None
            if self._compaction_due(_compaction.estimate_tools_tokens(turn_tools)):
                trim_notice = self._try_trim_tools(turn_tools)
                if trim_notice is not None:
                    self._append_notice("tools_trimmed", trim_notice)
                    yield Event(EventType.COMPACTED, {"text": trim_notice})
                    turn_tools, turn_tool_names, turn_tool_mode = self._tools_for_call()
                else:
                    yield Event(EventType.COMPACTING, {})
                    notice = await self._compact_now()
                    if notice:
                        # Room again �?the budget trim's job is done until the next
                        # pressure point.
                        self._tools_minimal = False
                        self._append_notice("compacted", notice)
                        yield Event(EventType.COMPACTED, {"text": notice})
                        turn_tools, turn_tool_names, turn_tool_mode = (
                            self._tools_for_call()
                        )

            turn: AssistantTurn | None = None
            streamed: list[str] = []
            streamed_reasoning: list[str] = []

            def _partial_turn() -> AssistantTurn:
                # What the user watched arrive �?text and thinking, NO tool calls (any
                # half-formed calls would either orphan or execute against the stop).
                return AssistantTurn(
                    text="".join(streamed) or None,
                    reasoning="".join(streamed_reasoning) or None,
                )

            # R5.1: safe-point — check for pending steers before model request.
            self._poll_control_channel()
            try:
                async for chunk in self._astream(
                    turn_tools, turn_tool_names, turn_tool_mode
                ):
                    if chunk.reasoning_delta:
                        streamed_reasoning.append(chunk.reasoning_delta)
                        yield Event(
                            EventType.REASONING_DELTA, {"text": chunk.reasoning_delta}
                        )
                    if chunk.text_delta:
                        streamed.append(chunk.text_delta)
                        yield Event(
                            EventType.ASSISTANT_DELTA, {"text": chunk.text_delta}
                        )
                    if chunk.turn is not None:
                        turn = chunk.turn
            except Exception as exc:  # provider failure
                # Bounded retry (Codex-absorbed, v0.3.0 P1): transient/transport
                # failures (429, 5xx, connection, TTFT stall) earn a bounded exponential
                # backoff. ONLY when nothing was streamed yet �?re-sending a turn whose
                # partial answer the user already watched arrive would duplicate it. The
                # finish_reason guard (stream_truncated) and context overflow are never
                # auto-retried here: the former stays loud by design, the latter belongs
                # to the compaction policy.
                if (
                    self.max_retries > 0
                    and self._turn_retries < self.max_retries
                    and not streamed
                    and not streamed_reasoning
                    and not self._cancel.is_cancelled()
                    and is_retryable(exc)
                ):
                    self._turn_retries += 1
                    delay = await wait_for_retry_async(
                        self._turn_retries - 1, exc=exc
                    )
                    self._append_notice(
                        "retrying",
                        f"Model call failed transiently ({classify_error(exc).value}) �?"
                        f"retrying (attempt {self._turn_retries}/{self.max_retries})",
                    )
                    yield Event(
                        EventType.ERROR,
                        {
                            "error": (
                                f"Transient model failure �?retrying after "
                                f"{delay * 1000:.0f}ms."
                            ),
                            "error_type": classify_error(exc).value,
                        },
                    )
                    continue
                # A raw context-overflow 400 (compaction mispredicted, e.g. the estimate
                # path) routes into the compaction policy instead of surfacing. The retry
                # is progress-guarded: each pass moves the boundary forward or gives up,
                # so a model that keeps overflowing still terminates in the error path.
                if _compaction.is_context_overflow(exc) and not self._cancel.is_cancelled():
                    yield Event(EventType.COMPACTING, {})
                    notice = await self._compact_now(force=True)
                    if notice:
                        self._append_notice("compacted", notice)
                        yield Event(EventType.COMPACTED, {"text": notice})
                        continue
                # Same contract as the stop path below: the partial the user watched
                # arrive survives the failure.
                if streamed or streamed_reasoning:
                    self.messages.append(_assistant_message(_partial_turn()))
                friendly = friendly_model_error(self.model, exc)
                error_class = classify_error(exc).value
                payload = {
                    "error": friendly or str(exc),
                    "error_type": error_class,
                    "error_class": error_class,
                }
                if friendly:
                    payload["raw"] = str(exc)
                self._append_notice("error", friendly or str(exc))
                yield Event(EventType.ERROR, payload)
                return
            if self._cancel.is_cancelled() and turn is None:
                # Stopped mid-stream: persist exactly what the user watched arrive.
                if streamed or streamed_reasoning:
                    self.messages.append(_assistant_message(_partial_turn()))
                self._append_notice("interrupted")
                yield Event(EventType.INTERRUPTED, {"iterations": iterations})
                return
            if turn is None:
                turn = AssistantTurn()
            if turn.usage is not None:
                # The trigger signal: the prompt-side total that actually occupied the
                # window on this round-trip (estimate fallback when never reported).
                self._last_context_tokens = turn.usage.context_tokens

            self.messages.append(_assistant_message(turn, model=self.model))
            payload: dict[str, Any] = {
                "text": turn.text,
                "tool_calls": [tc.name for tc in turn.tool_calls],
            }
            if turn.reasoning:
                payload["reasoning"] = turn.reasoning
            if turn.usage is not None:
                payload["usage"] = {"model": self.model, **turn.usage.as_dict()}
            yield Event(EventType.ASSISTANT_MESSAGE, payload)

            if not turn.tool_calls:
                if self._steering:
                    self._inject_steering()
                    continue
                # The model tried to call a tool and the syntax never parsed �?salvage
                # already had its go. Ending as "completed" here would present a
                # half-written call as the answer, which is indistinguishable from the
                # model deciding it was done; the user just sees narration trailing off
                # into stray tags. Fail loudly on the error path so the UI offers Retry �?
                # this is drift, not a deterministic failure, so retrying the same model
                # usually works.
                if looks_like_unparsed_tool_call(
                    turn.text, self.registry.schemas() or None
                ):
                    # Tool-injection escape hatch (v0.3.0 P0): the model is writing
                    # tool-call markup for a tool it couldn't SEE this call (the
                    # selection withheld it). One miss flips the session to full
                    # injection and retries the iteration �?a keyword miss must cost
                    # payload, never the turn. Genuine format drift (no named tool)
                    # still ends as the retriable error below.
                    if self.tool_selection == "auto" and not self._tool_expanded:
                        missed = _tool_selection.named_withheld_tools(
                            turn.text, turn_tool_names, self.registry.names()
                        )
                        if missed:
                            self._tool_expanded = True
                            self._tools_minimal = False
                            message = (
                                "The model reached for tool(s) " + ", ".join(missed) +
                                " that weren't injected this turn �?retrying with the "
                                "full toolset for the rest of this conversation."
                            )
                            self._append_notice("tools_expanded", message)
                            continue
                    message = (
                        f"{self.model} replied with a tool call this endpoint couldn't "
                        "parse, so the turn was stopped rather than answered from a "
                        "partial call. Retry, or switch to a larger model �?smaller "
                        "local models drift off the tool-call format, especially with "
                        "many tools in play."
                    )
                    self._append_notice("error", message)
                    yield Event(
                        EventType.ERROR,
                        {"error": message, "error_type": "UnparsedToolCall"},
                    )
                    return
                yield Event(
                    EventType.TURN_END,
                    {"status": "completed", "iterations": iterations},
                )
                return

            # R5.1: safe-point — check for pending steers before tool dispatch.
            self._poll_control_channel()
            async for event in self._tool_lifecycle.authorize_and_execute(
                turn.tool_calls
            ):
                yield event

            yield Event(EventType.ITERATION_END, {"iteration": iterations})

            if self._cancel.is_cancelled():
                self._append_notice("interrupted")
                yield Event(EventType.INTERRUPTED, {"iterations": iterations})
                return
            if self._steering:
                self._inject_steering()

    # -- auto-compaction (OPE-27) ------------------------------------------------
    def _compaction_config(self) -> dict[str, Any]:
        cfg = dict(self.compaction_settings() or {}) if self.compaction_settings else {}
        if not cfg.get("context_window"):
            from providers.matrix import model_context_windows

            cfg["context_window"] = model_context_windows().get(self.model)
        cfg.setdefault("threshold_pct", _compaction.DEFAULT_THRESHOLD_PCT)
        cfg.setdefault("cap_tokens", _compaction.DEFAULT_CAP_TOKENS)
        return cfg

    def _compaction_due(self, extra_tokens: int = 0) -> bool:
        """The trigger check alone �?cheap and side-effect free, so the loop can emit
        the COMPACTING signal before committing to the (slow) summarizer call. Tool
        schemas occupy the window like messages do, so the estimate path adds them
        (`extra_tokens`); the reported-usage path already includes whatever rode along
        on that call, but adding the CURRENT call's schemas keeps the check conservative
        either way."""
        cfg = self._compaction_config()
        if cfg.get("enabled") is False:
            return False
        if self._last_context_tokens is not None:
            signal = self._last_context_tokens
        else:
            # Weighted prefill accounting (Codex-absorbed): when the config sets
            # compaction_prefill_weight < 1.0, the estimate reflects that prefill
            # (input) tokens cost less than sampling tokens on shared gateways �?so a
            # big prompt still triggers, but not as early as raw chars/4 would.
            weight = float(cfg.get("prefill_weight", 1.0))
            if weight != 1.0:
                signal = _compaction.estimate_tokens_weighted(
                    self._outbound_messages(), prefill_weight=weight
                )
            else:
                signal = _compaction.estimate_tokens(self._outbound_messages())
        signal += extra_tokens
        return _compaction.should_compact(
            signal,
            cfg.get("context_window"),
            threshold_pct=float(cfg["threshold_pct"]),
            cap_tokens=int(cfg["cap_tokens"]),
        )

    def _tools_for_call(self) -> tuple[list[dict[str, Any]] | None, list[str], str]:
        """The tool schemas for THIS model call (v0.3.0 P0 tool injection): the relevant
        subset per the tool-selection policy, or everything under "full" / after the
        escape hatch fired. Returns (schemas, names, mode_label) for the request log."""
        if self.tool_selection != "auto" or self._tool_expanded:
            names = self.registry.names()
            return (self.registry.schemas() or None), names, "full"
        # Read-only awareness (v0.3.0 P1): a plan/discuss phase (READ_ONLY_MODES) or a
        # read-only task must not see write/exec tools �?cut payload AND the write
        # surface on exactly the turns where acting isn't on the table.
        from core.permissions import READ_ONLY_MODES

        read_only = self.permissions.mode in READ_ONLY_MODES or _tool_selection.is_readonly_turn(
            self.messages
        )
        names = _tool_selection.select_tool_names(
            self.registry.names(),
            self.messages,
            family=self.agent_family,
            minimal=self._tools_minimal,
            read_only=read_only,
        )
        return (self.registry.schemas(names) or None), names, "selected"

    def _try_trim_tools(self, turn_tools: list[dict[str, Any]] | None) -> str | None:
        """Context-budget step 1 (v0.3.0 P0): when history + tool schemas would breach
        the trigger, first try shedding everything but the core toolset �?tool schemas
        are the cheapest payload to drop (no knowledge lost, no compaction call). Returns
        the user-facing notice when the trim applies, else None (�?full compaction)."""
        if self.tool_selection != "auto" or self._tools_minimal or self._tool_expanded:
            return None
        cfg = self._compaction_config()
        core_names = sorted(_tool_selection.CORE_TOOLS & set(self.registry.names()))
        msg_signal = self._last_context_tokens or _compaction.estimate_tokens(
            self._outbound_messages()
        )
        trigger = _compaction.trigger_tokens(
            cfg.get("context_window"),
            threshold_pct=float(cfg["threshold_pct"]),
            cap_tokens=int(cfg["cap_tokens"]),
        )
        if msg_signal + _compaction.estimate_tools_tokens(
            self.registry.schemas(core_names)
        ) > trigger:
            return None  # even core-only wouldn't fit �?compaction must run
        if len(core_names) >= len(turn_tools or []):
            return None  # already (effectively) minimal
        self._tools_minimal = True
        return (
            f"Context is tight �?trimmed the injected toolset to the core "
            f"{len(core_names)} tool(s) to keep the next model call within budget."
        )

    async def _compact_now(self, *, force: bool = False) -> str | None:
        """Run the compaction policy. Callers gate on `_compaction_due()` (or `force`,
        the overflow path). Returns the user-facing notice text when the outbound view
        changed, else None. Failure policy per spec: retry once (both modes); attended �?
        Retry / Trim prompt; unattended �?auto-trim and continue (never park a run on
        bookkeeping)."""
        cfg = self._compaction_config()
        pct = float(cfg["threshold_pct"])
        cap = int(cfg["cap_tokens"])
        window = cfg.get("context_window")
        keep = int(
            _compaction.KEEP_RECENT_FRACTION
            * _compaction.trigger_tokens(window, threshold_pct=pct, cap_tokens=cap)
        )
        model = str(cfg.get("model") or "") or self.model

        def _build() -> _compaction.CompactionState | None:
            return _compaction.build_state(
                self.messages,
                provider=self.provider,
                model=model,
                keep_tokens=keep,
                prior=self.compaction_state,
            )

        state: _compaction.CompactionState | None = None
        failed = False
        for _attempt in range(2):  # first try + the unconditional single retry
            try:
                state = await asyncio.to_thread(_build)
                failed = False
                break
            except Exception:
                failed = True
        if failed and self.question_asker is not None and self.is_attended and self.is_attended():
            while True:
                answer = await self._interruptible(
                    self.question_asker(
                        {
                            "question": (
                                "Context compaction failed �?the summarizer couldn't "
                                "condense this session's history. How should I proceed?"
                            ),
                            "options": ["Retry", "Trim oldest 10%"],
                            "allow_text": False,
                            "header": "Compaction",
                        },
                        None,
                    ),
                    interrupted=None,
                )
                if not answer or answer.get("answer") != "Retry":
                    break
                try:
                    state = await asyncio.to_thread(_build)
                    failed = False
                    break
                except Exception:
                    continue
        if state is not None:
            self.compaction_state = state
            self._last_context_tokens = None  # stale once the outbound view shrank
            return "Context compacted �?earlier turns were summarized"
        if failed or force:
            trimmed = _compaction.trim_state(self.messages, prior=self.compaction_state)
            if trimmed is not None:
                self.compaction_state = trimmed
                self._last_context_tokens = None
                return "Context trimmed �?oldest turns dropped (summary unavailable)"
        return None

    # -- helpers ----------------------------------------------------------------
    async def _astream(
        self,
        tools: list[dict[str, Any]] | None,
        tool_names: list[str],
        tool_mode: str,
    ):
        """Bridge the provider's blocking stream generator to the async loop via a
        thread + queue, so text deltas surface live without blocking the event loop.
        Also the request-observability chokepoint (v0.3.0 P0): exactly one log row per
        model call �?payload size, tool count, TTFT, outcome."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        model, messages, settings = (
            self.model,
            self._outbound_messages(),
            self.model_settings,
        )
        provider = self.provider
        # Stops the producer mid-stream (TTFT stall) without touching `self._cancel` �?
        # the user-interrupt flag belongs to the USER, not to a retry guard.
        abort = threading.Event()

        # One observation per call, written no matter how the stream ends. TTFT is
        # measured at the first chunk (the timeout killer on free/shared nodes);
        # `context_tokens` comes from the provider's reported usage when it reports.
        _started = time.perf_counter()
        _ttft: list[float] = []
        _usage: list[Any] = []
        _state: dict[str, Any] = {"outcome": "ok", "error_type": None, "error_class": None}

        def _observe() -> None:
            if self.request_logger is None:
                return
            try:
                body = json.dumps(
                    {"messages": messages, "tools": tools}, default=str
                ).encode("utf-8")
                usage = _usage[0] if _usage else None
                self.request_logger(
                    {
                        "ts": time.time(),
                        "provider": type(provider).__name__,
                        "model": model,
                        "messages_count": len(messages),
                        "body_bytes": len(body),
                        "tools_count": len(tools or []),
                        "tool_mode": tool_mode,
                        "tool_names": tool_names,
                        "context_estimate_tokens": len(body) // 4,
                        "ttft_ms": (
                            round((_ttft[0] - _started) * 1000) if _ttft else None
                        ),
                        "duration_ms": round((time.perf_counter() - _started) * 1000),
                        "outcome": _state["outcome"],
                        "error_type": _state["error_type"],
                        "error_class": _state["error_class"],
                        "context_tokens": (
                            getattr(usage, "context_tokens", None) if usage else None
                        ),
                    }
                )
            except Exception:
                pass  # observability must never break the turn

        def produce():
            chunks = None
            try:
                chunks = provider.stream(
                    model=model, messages=messages, tools=tools, **settings
                )
                for chunk in chunks:
                    # User pressed Stop, or the TTFT guard aborted this attempt �?drop
                    # the stream between chunks (reading the flags from a thread is
                    # safe; we only read).
                    if self._cancel.is_cancelled() or abort.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as exc:  # surfaced to the awaiting consumer
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                # Cancel must propagate INTO the provider: closing the generator
                # raises GeneratorExit at its yield point, which tears down the
                # in-flight HTTP request instead of leaving it streaming to a
                # consumer that already left (the GC would only get to it later).
                # chunks stays None when stream() itself raised �?that failure was
                # already surfaced as an error event above.
                if chunks is not None:
                    try:
                        chunks.close()
                    except Exception:
                        pass
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        loop.run_in_executor(None, produce)
        try:
            while True:
                # Race the queue against Stop so a stalled stream (no chunks arriving �?
                # the pre-first-token wait, a wedged connection) can't hold the turn.
                # While waiting for the FIRST token, the TTFT ceiling bounds the wait;
                # once streaming, only a user Stop can end it.
                get_task = asyncio.ensure_future(queue.get())
                cancel_task = asyncio.ensure_future(self._cancel.wait())
                wait_timeout = self.ttft_timeout if not _ttft else None
                done, _ = await asyncio.wait(
                    {get_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=wait_timeout,
                )
                cancel_task.cancel()
                if get_task not in done:
                    get_task.cancel()
                    if self._cancel.is_cancelled():
                        # User stop �?interrupted, not a stall.
                        _state["outcome"] = "interrupted"
                        return  # interrupted �?the producer exits on its own next chunk
                    # TTFT guard (v0.3.0 P1): the pre-first-token wait exceeded the
                    # ceiling �?a stall, not a user stop. Classify it and let the
                    # retry policy decide (it IS retryable: nothing was delivered).
                    abort.set()
                    _state["outcome"] = "error"
                    _state["error_type"] = TTFTTimeoutError.__name__
                    _state["error_class"] = ErrorClass.TTFT_TIMEOUT.value
                    raise TTFTTimeoutError(
                        "No first token arrived within "
                        f"{self.ttft_timeout:.0f}s �?the upstream stalled or the "
                        "gateway is overloaded."
                    )
                kind, payload = get_task.result()
                if kind == "chunk":
                    if not _ttft:
                        _ttft.append(time.perf_counter())
                    if payload.turn is not None and payload.turn.usage is not None:
                        _usage.append(payload.turn.usage)
                    yield payload
                elif kind == "error":
                    _state["outcome"] = "error"
                    _state["error_type"] = type(payload).__name__
                    _state["error_class"] = classify_error(payload).value
                    raise payload
                else:
                    return
        finally:
            _observe()

    def _inject_steering(self) -> None:
        for text, source in self._steering:
            message: dict[str, Any] = {
                "role": "user",
                "content": text,
                "ts": time.time(),
            }
            if source is not None:
                message["source"] = source
            self.messages.append(message)
        self._steering = []

    def _poll_control_channel(self) -> None:
        """R5.1 B3/B4: check for pending steers at a safe point.

        Looks up the RunControlChannel for the current run_id (via
        runscope) and, if a steer is REQUESTED, accepts it and injects
        it as a steering message so the model sees the user's
        mid-execution direction change.

        This is called at safe points: before model requests, before
        tool-call dispatch, and between iterations.
        """
        try:
            from core.runscope import current as _current_scope
            from core.steering import get_control_channel, SteerSafePoint

            scope = _current_scope()
            if scope is None:
                return
            run_id = scope[0]
            ch = get_control_channel(run_id)
            if ch is None:
                return
            if ch.is_cancelled:
                self.request_interrupt()
                return
            steer = ch.steers.poll()
            if steer is not None:
                ch.steers.accept(steer.steer_id)
                self._steering.append((steer.content, {"source": "steer"}))
                ch.apply_steer(
                    steer.steer_id,
                    safe_point=SteerSafePoint.APPLY_NOW,
                )
        except Exception:
            pass

    def _outbound_messages(self) -> list[dict[str, Any]]:
        """`self.messages` prepared for the provider. The SOLE provider feed (see `_astream`).

        Every message is stripped of the display-only sidecars �?`source`, `_display`, and
        `ts` �?(providers reject unknown keys), unconditionally �?whether or not a
        `<system-context>` block is added. When a context
        provider yields a non-empty string, an ephemeral `<system-context>` block is appended to the
        last user message. Never mutates `self.messages`, so neither the strip nor the block is
        persisted/replayed.
        """
        # Strip the display-only sidecars �?`source` (connector cards), `_display`
        # (e.g. filter-hidden counts), `ts` (append-time timestamps), `reasoning`
        # (thinking text), and `usage` (token counts) �?copying only messages that carry
        # one. Whole `notice` messages (error/interrupted/model-switch markers) are
        # display-only too: dropped entirely.
        _SIDECARS = ("source", "_display", "ts", "reasoning", "usage")
        # Auto-compaction (OPE-27): everything before the boundary is represented by the
        # compacted block. Outbound-only �?the canonical history stays intact �?and the
        # block+tail are byte-stable between turns, so prompt caching keeps working.
        source_messages = _compaction.apply_to_outbound(
            self.messages, self.compaction_state
        )
        out = [
            (
                {k: v for k, v in msg.items() if k not in _SIDECARS}
                if any(s in msg for s in _SIDECARS)
                else msg
            )
            for msg in source_messages
            if msg.get("role") != "notice"
        ]
        # PDF attachments (stored as `file` parts) are adapted to the ACTIVE model right
        # here �?never in the persisted history �?so a mid-session model switch always
        # re-decides: native PDF models get the real document, the rest get the local
        # text-extract/page-image fallback (pdf_support.py).
        if any(
            isinstance(p, dict) and p.get("type") == "file"
            for msg in out
            if isinstance(msg.get("content"), list)
            for p in msg["content"]
        ):
            caps = self.provider.capabilities(self.model)
            if not getattr(caps, "pdf", False):
                from core import pdf_support

                out = [
                    (
                        {
                            **msg,
                            "content": pdf_support.adapt_content(msg["content"], caps),
                        }
                        if isinstance(msg.get("content"), list)
                        else msg
                    )
                    for msg in out
                ]

        # Images get the same per-turn treatment: a model without vision receives a visible
        # placeholder instead of a payload it would reject. Like the PDF path, this re-decides
        # per call, so a mid-session switch to/from a vision model always does the right thing.
        if any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for msg in out
            if isinstance(msg.get("content"), list)
            for p in msg["content"]
        ):
            caps = self.provider.capabilities(self.model)
            if not getattr(caps, "vision", False):
                placeholder = {
                    "type": "text",
                    "text": "[image attachment �?not viewable by this model]",
                }
                out = [
                    (
                        {
                            **msg,
                            "content": [
                                (
                                    placeholder
                                    if isinstance(p, dict)
                                    and p.get("type") == "image_url"
                                    else p
                                )
                                for p in msg["content"]
                            ],
                        }
                        if isinstance(msg.get("content"), list)
                        else msg
                    )
                    for msg in out
                ]

        context = (
            self.context_provider() if self.context_provider is not None else ""
        ) or ""
        if not context:
            return out
        block = f"\n\n<system-context>\n{context}\n</system-context>"
        for i in range(len(out) - 1, -1, -1):
            if out[i].get("role") != "user":
                continue
            msg = dict(out[i])
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = content + block
            elif isinstance(content, list):  # content-parts (text + images)
                msg["content"] = [*content, {"type": "text", "text": block}]
            else:
                msg["content"] = block
            out[i] = msg
            break
        return out


