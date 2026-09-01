"""Session lifecycle: engine build/save/persist, autotitle, roots-aware
session records, listing and running-state marks.

Extracted verbatim from SessionManager (see manager.py); composed back via
mixin inheritance so behavior is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from core.agent import build_engine
from core.agents import get_agent
from core.conversations import title_from
from core.engine import Approver, TurnEngine
from core.permissions import Mode
from core.runtime import RuntimePort, TurnEngineAdapter
from core.sessions import SessionRecord
from services.server.manager_support import logger


from services.server.manager_contract import ManagerHostState


class SessionsMixin(ManagerHostState):

    def _bind_runtime(self, engine: TurnEngine, session_id: str) -> RuntimePort:
        """Wrap a freshly built TurnEngine into the application-layer RuntimePort.
        The ledger binding makes every driven turn a durable run (docs/architecture/adr/ADR-001-run-event-ledger.md);
        storing ONLY adapters means the business layer never holds a bare TurnEngine.
        The executor's process-event observer rides along, so background-process
        spawn/kill facts land in the run ledger of the turn that caused them."""
        executor = getattr(engine, "executor", None)
        if executor is not None:
            executor.process_event_sink = self._record_process_event
        return TurnEngineAdapter(
            engine, ledger=self.run_ledger, session_id=session_id
        )

    def _record_process_event(self, event: dict[str, Any]) -> None:
        """Persist one background-process lifecycle fact (spawn/kill).

        Inside a driven run (the usual case — `run_in_background`, `shell_task_kill`)
        the ambient run scope names the owning run: append a durable ledger event.
        Outside any run (managed-task teardown at session/app shutdown) there is no
        run to attribute to, so the fact goes to the session-scoped audit trail
        instead — durable either way, never silently dropped."""
        from core import runscope
        from packages.sanitize import sanitize_payload

        payload = sanitize_payload(
            {
                key: value
                for key, value in event.items()
                if key != "event"  # the event type travels in its own column
            }
        )
        scope = runscope.current()
        if scope is not None:
            run_id, _session_id = scope
            try:
                self.run_ledger.append(
                    run_id, event["event"], actor="tool", payload=payload
                )
            except Exception:
                pass
            return
        try:
            self.audit_store.append(
                {
                    "tool": "run_shell",
                    "stage": event["event"],
                    "status": str(event.get("reason") or "teardown"),
                    "reason": json.dumps(payload, default=str),
                }
            )
        except Exception:
            pass

    def get_engine(
        self,
        session_id: str,
        *,
        workspace: str | None = None,
        agent: str = "code",
        approver: Approver | None = None,
        extra_tools: list[Any] | None = None,
        directory_requester: Any | None = None,
        plan_approver: Any | None = None,
        question_asker: Any | None = None,
    ) -> RuntimePort | None:
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            # bind() ignores None arguments — only supplied callbacks rebind.
            runtime.bind(
                approver=approver,
                directory_requester=directory_requester,
                plan_approver=plan_approver,
                question_asker=question_asker,
            )
            return runtime

        record = self.session_store.load(session_id)
        is_new_session = record is None
        agent_name = (record.agent if record else agent) or "code"
        ag = get_agent(agent_name)

        if record:
            ws = record.workspace or None
            model, mode, messages = record.model, Mode(record.mode), record.messages
        else:
            ws = self.resolve_workspace(workspace) if ag.needs_workspace else None
            model, mode, messages = self.model, self.mode, None

        if ag.needs_workspace and (not ws or not Path(ws).is_dir()):
            # Knowledge surfaces (Cowork, Ops, …) start "orphan": no folder picked →
            # auto-provision a per-conversation scratch directory (generalizes MyHelper's
            # auto-workspace). Code-family surfaces still require a real repo; Chat needs none.
            if ag.family == "knowledge":
                ws = self._provision_scratch(session_id)
            else:
                return None

        if ws:
            self.session_store.touch_workspace(ws)
        # Orphan surfaces are multi-root: the scratch (ws) is the primary writable root, plus any
        # folders the user added (persisted per session). Code/Chat stay single-root (roots=None).
        roots = None
        if ag.family == "knowledge" and ws:
            extra = [
                r
                for r in ((record.extra_roots if record else []) or [])
                if Path(str(r.get("path", ""))).is_dir()
            ]
            roots = [{"path": ws, "writable": True, "label": "scratch"}, *extra]
        engine = build_engine(
            agent=ag,
            workspace=ws,
            model=model,
            mode=mode,
            provider=self.provider,
            # Per-session reasoning depth (Settings-free control on the session card):
            # flows through model_settings into every provider call. "auto" sends nothing
            # — the provider keeps its own default.
            model_settings=(
                {"reasoning_effort": record.reasoning_effort}
                if record and record.reasoning_effort not in ("", "auto")
                else None
            ),
            # Memory off (§4.3) = stop LEARNING, not amnesia: saved facts still inject
            # and stay usable, only the write tools go. Read at build time; running
            # sessions finish under the mode they started with.
            memory_store=self.memory_store,
            memory_off=not self.memory_settings.enabled,
            # LIVE, not a snapshot: turning saving off mid-conversation must take
            # effect at once (owner-hit 2026-07-28 — a running session kept saving).
            memory_saving_enabled=lambda: self.memory_settings.enabled,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open (same reason as the saving switch).
            user_rules=lambda: self.memory_settings.user_rules,
            on_memory_saved=self._memory_saved_notifier(session_id),
            messages=messages,
            extra_tools=extra_tools,
            secrets=self.secrets,
            task_store=self.task_store,
            wake_store=self.wakes,
            session_id=session_id,
            audit_sink=self.audit_store.append,
            roots=roots,
            # WS sessions pass mode-aware callbacks (attended → live prompt, unattended → Inbox).
            # Background / self-wake / durable-resume runs have no live socket → default to the
            # Inbox-based callbacks so a rebuilt engine can still get approvals/answers (and, on
            # resume, the already-resolved item returns immediately).
            approver=approver or self.inbox_approver(session_id, agent),
            directory_requester=directory_requester
            or self.inbox_directory_requester(session_id, agent),
            plan_approver=plan_approver or self.inbox_plan_approver(session_id, agent),
            question_asker=question_asker
            or self.inbox_question_asker(session_id, agent),
            subscription_store=self.subscriptions,
            channel_buffer=self.channel_buffer,
            routing_targets=self._routing_targets(session_id, agent),
            # Per-session connection hierarchy: expose only effective-enabled connectors' tools.
            connector_filter=self.effective_connectors(session_id, agent_name),
            # Per-session skill menu, LIVE (SKILLS-SPEC §3): a callable so load_skill sees
            # disables/new skills immediately; the catalog snapshot is taken at build.
            skill_filter=lambda sid=session_id, w=ws: self.effective_skill_names(sid, w),
        )
        # Wrap into the RuntimePort immediately: every later touch (task grants,
        # mention rules, persistence, turn driving) goes through the port surface.
        runtime = self._bind_runtime(engine, session_id)
        # An automation run rebuilt here (manual "Run now" over WS, durable resume) still
        # carries its task's standing allowances — the rules live on the task record.
        owning_task = self.task_store.task_for_run_session(session_id)
        if owning_task is not None:
            self._seed_task_permissions(runtime, owning_task)
        # A mention-spawned session (§31) keeps its in-thread reply pre-approved across
        # rebuilds/restarts — the grant is re-derived from the durable thread map.
        for thread_target in self.mention_sessions.targets_for(session_id):
            runtime.add_task_rule("send_message", thread_target)
        if record is not None and record.grants:
            self._apply_grants(runtime, record.grants)
        # Auto-compaction (OPE-27): restore the persisted view boundary and wire the live
        # Settings getter — post-construction, so build_engine's signature stays put.
        if record is not None and record.compaction:
            from core.compaction import CompactionState

            runtime.set_compaction_state(CompactionState.from_dict(record.compaction))
        runtime.set_compaction_settings(self.compaction_settings)
        self._runtimes[session_id] = runtime
        if is_new_session:
            self._emit_session_created(session_id, agent_name)
        return runtime


    def persist_session(self, session_id: str) -> None:
        """Save the cached engine's thread (so a prompt's pending tool call survives a crash)."""
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            self.save(session_id, runtime)


    def save(self, session_id: str, runtime: RuntimePort) -> None:
        cwd = runtime.workspace_dir
        self.session_store.save(
            SessionRecord(
                session_id=session_id,
                workspace=os.path.realpath(cwd) if cwd else "",
                model=runtime.model,
                mode=runtime.mode.value,
                messages=runtime.messages,
                title=title_from(runtime.messages),
                agent=runtime.agent_name,
                extra_roots=self._extra_roots_of(runtime),
                grants=runtime.session_grants(),
                compaction=runtime.compaction_dict(),
                reasoning_effort=runtime.reasoning_effort,
            )
        )


    @staticmethod
    def _apply_grants(runtime: RuntimePort, grants: dict[str, Any]) -> None:
        """Re-apply a reloaded session's persisted "Always allow" approvals — they're
        session-scoped, and the session outlives the process (owner-hit 2026-07-22)."""
        for tool in grants.get("tools") or []:
            runtime.grant_tool(str(tool))
        for command in grants.get("commands") or []:
            runtime.grant_command(str(command))


    @staticmethod
    def _extra_roots_of(runtime: RuntimePort) -> list[dict[str, Any]]:
        """Added folders = the runtime's roots minus the primary scratch (index 0)."""
        roots = runtime.list_roots()
        return [
            {"path": str(r.path), "writable": bool(r.writable), "label": r.label}
            for r in roots[1:]
        ]


    # -- LLM auto-titles (FB-010) -------------------------------------------------
    _AUTOTITLE_PROMPT = (
        "You title chat sessions. Given the user's opening message(s), reply with ONLY "
        "a 4-5 word title for the session — no quotes or punctuation wrapping it. If "
        'the opening is merely a greeting or small-talk with no topic ("hey", '
        '"how are you", "hi there"), reply with exactly: small-talk'
    )

    def _maybe_autotitle(self, session_id: str) -> None:
        """Kick off title generation after a turn completes, fire-and-forget. Only while
        the session has neither a manual rename nor a generated title, at most twice:
        attempt 1 rides turn 1, and the second window exists solely for the small-talk
        retry (with both openers). Attempts are counted in memory rather than derived
        from the user-message count — steering injections also land as role "user", and
        counting them would silently suppress titling on a steered first turn. A restart
        forgetting the counter is harmless: renamed/auto_title still gate re-titling."""
        if session_id.startswith("__"):
            return
        runtime = self._runtimes.get(session_id)
        if runtime is None or session_id in self._autotitle_inflight:
            return
        if self.task_store.task_for_run_session(session_id) is not None:
            return  # automation runs are titled by their task
        if self._autotitle_attempts.get(session_id, 0) >= 2:
            return
        users = [m for m in runtime.messages if m.get("role") == "user"]
        if not users:
            return
        state = self.session_store.title_state(session_id)
        if state is None or state["renamed"] or state["auto_title"]:
            return
        from core.attachments import content_to_text

        openers = [
            text
            for m in users
            if (text := content_to_text(m.get("content"), image_placeholder="").strip())
        ][:2]
        if not openers:
            return
        self._autotitle_attempts[session_id] = (
            self._autotitle_attempts.get(session_id, 0) + 1
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop to ride (sync caller) — skip, never block
        self._autotitle_inflight.add(session_id)
        # Retain the task: the loop holds only a weak ref, and a GC'd task would both
        # kill the title mid-flight and strand the inflight guard.
        task = loop.create_task(
            self._generate_autotitle(session_id, runtime.model, openers)
        )
        self._autotitle_tasks.add(task)
        task.add_done_callback(self._autotitle_tasks.discard)


    async def _generate_autotitle(
        self, session_id: str, model: str, openers: list[str]
    ) -> None:
        """One cheap non-streaming completion on the session's own provider/model. Every
        failure (provider error, empty, absurdly long) is swallowed — the title_from
        fallback stays; the small-talk sentinel leaves auto_title unset so the turn-2
        retry can run. The manager's provider IS the session engine's provider (the
        same object flows through build_engine), so no runtime escape hatch is needed."""
        try:
            turn = await asyncio.to_thread(
                self.provider.complete,
                model=model,
                messages=[
                    {"role": "system", "content": self._AUTOTITLE_PROMPT},
                    {"role": "user", "content": "\n\n".join(openers)},
                ],
                temperature=0.2,
                # Reasoning-routed models spend hidden tokens BEFORE emitting text; a
                # tight cap plus default effort yields an empty completion and a silent
                # no-op. Effort "none" reaches only the OpenAI-compat path (the native
                # providers whitelist their settings), and 64 leaves headroom either way.
                max_tokens=64,
                reasoning_effort="none",
            )
            raw = (getattr(turn, "text", None) or "").strip()
            # Sanitize: surrounding quotes off, whitespace collapsed, capped at 60.
            title = " ".join(raw.strip("\"'“”‘’`").split())
            # Sentinel tolerance: models riff on the exact token ("Small talk.", quoted,
            # trailing period) — normalize before comparing, else the riff becomes the title.
            if title.lower().strip(".!,;:'\"").replace(" ", "-").replace("_", "-") in (
                "small-talk",
                "smalltalk",
            ):
                return
            if not title or len(title) > 80:
                return
            if self.session_store.set_auto_title(session_id, title[:60]):
                # Best-effort nudge for any live viewer; the sidebar's poll and
                # post-turn refresh pick the new title up regardless.
                await self.broadcast_session(
                    session_id,
                    "session_title",
                    {"session_id": session_id, "title": title[:60]},
                )
        except Exception:
            # A failed title must never surface as a session error — but it must
            # not be invisible either (a silent provider 400 hid the max_tokens
            # rejection for a whole owner test pass, 2026-07-20).
            logger.debug("autotitle failed for %s", session_id, exc_info=True)
        finally:
            self._autotitle_inflight.discard(session_id)


    def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        # A live engine's in-memory thread is authoritative: mid-turn it's ahead of the
        # persisted record — which may not even exist yet for a scheduled run's first turn
        # (opening a "running" automation showed a blank session; owner report 2026-07-04).
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            return list(runtime.messages)
        record = self.session_store.load(session_id)
        return record.messages if record else []


    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be renamed"}
        ok = self.session_store.rename(session_id, title)
        return {
            "ok": ok,
            "session_id": session_id,
            "title": " ".join((title or "").split())[:120],
        }


    def set_session_flags(
        self,
        session_id: str,
        *,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be modified here"}
        ok = self.session_store.set_flags(session_id, pinned=pinned, archived=archived)
        return {"ok": ok, "session_id": session_id}


    async def revert_session(self, session_id: str, index: int) -> dict[str, Any]:
        """opencode-style revert: drop messages from `index` onward (the user message at
        that index and everything after), keeping the prior context. Returns the original
        user text so the GUI can prefill the composer for editing. Works MID-TURN too:
        an in-flight turn is interrupted first (the rest of its reasoning would be wasted
        tokens anyway — the model re-answers from the edited content), then truncated."""
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be reverted here"}
        if self.is_running(session_id):
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                return {"ok": False, "error": "session is running"}
            runtime.interrupt()
            deadline = time.monotonic() + 15
            while self.is_running(session_id) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            if self.is_running(session_id):
                return {"ok": False, "error": "turn did not stop in time"}
        dropped = self.session_store.revert(session_id, index)
        if not dropped:
            return {"ok": False, "error": "nothing to revert at that index"}
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            runtime.truncate_messages(index)
            self.save(session_id, runtime)
        # The FIRST dropped message IS the user message being edited — later entries in
        # the dropped slice are the assistant's replies to it (scanning reversed() used
        # to prefill the composer with the ANSWER instead of the question).
        from core.attachments import content_to_text

        user_text = content_to_text(
            (dropped[0] or {}).get("content"), image_placeholder=""
        ).strip()
        return {"ok": True, "text": user_text}


    def set_reasoning_effort(self, session_id: str, effort: str) -> dict[str, Any]:
        """Persist the session's reasoning effort ("auto"/"low"/"high"/"max") and apply it
        to the live engine if one exists. The next engine build re-reads it from the store."""
        allowed = {"auto", "low", "high", "max"}
        effort = (effort or "auto").strip().lower()
        if effort not in allowed:
            return {"ok": False, "error": f"effort must be one of {sorted(allowed)}"}
        record = self.session_store.load(session_id)
        runtime = self._runtimes.get(session_id)
        if record is None and runtime is None:
            return {"ok": False, "error": "unknown session"}
        # A connected, never-sent session intentionally has no store row yet. Apply the
        # selection to its live engine; the first turn checkpoint creates the row with it.
        if record is not None:
            record.reasoning_effort = effort
            self.session_store.save(record)
        if runtime is not None:
            # Apply to the live engine immediately: model_settings flows into every
            # provider call (the next build re-reads it from the store).
            runtime.set_reasoning_effort(effort)
        return {"ok": True, "reasoning_effort": effort}


    def delete_session(self, session_id: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be deleted here"}
        runtime = self._runtimes.pop(session_id, None)
        self._session_event_sequences.pop(session_id, None)
        self._app_event_sequences.pop(session_id, None)
        self._autotitle_attempts.pop(session_id, None)
        if runtime is not None:
            try:
                # (was engine.interrupt() — a method that never existed; the AttributeError
                # was silently swallowed, so deleting a running session never stopped it.)
                runtime.interrupt()
            except Exception:
                pass
            # Managed background tasks die with the session; detached
            # (detach=true) tasks are deliberately left running.
            runtime.shutdown_executor()
        record = self.session_store.load(session_id)
        ok = self.session_store.delete(session_id)
        # Deleting a session is the one implicit unsubscribe (otherwise subscriptions are permanent).
        self.subscriptions.remove_session(session_id)
        # ...and releases any Slack threads it owned (§31): the next tag there spawns fresh.
        self.mention_sessions.remove_session(session_id)
        # ...and drops its per-session connector overrides (§4.2, like subscriptions).
        self.session_connections.remove_session(session_id)
        # ...and its per-session skill mutes (SKILLS-SPEC §3 — mutes die with the session).
        self.session_skills.remove_session(session_id)
        # ...and closes its pending Inbox items — an orphaned approval/question can never be
        # meaningfully answered (owner call, 2026-07-03).
        self.inbox.resolve_session(session_id)
        # ...and its scratch dir. STRICTLY scoped: only a directory inside scratch_base is
        # removed — a real project folder the user picked is never touched.
        if ok and record and record.workspace:
            scratch = self.scratch_base().resolve()
            ws = Path(record.workspace)
            try:
                resolved = ws.resolve()
                if (
                    resolved.is_relative_to(scratch)
                    and resolved != scratch
                    and resolved.is_dir()
                ):
                    shutil.rmtree(resolved)
            except OSError:
                pass  # a stale/foreign path must not fail the delete
        return {"ok": ok, "session_id": session_id}


    # -- read models ------------------------------------------------------------
    def list_sessions(self, workspace: str | None = None) -> list[dict[str, Any]]:
        ws = self.resolve_workspace(workspace) if workspace else None
        return [
            {
                "session_id": r.session_id,
                "title": r.title or "New session",
                "workspace": r.workspace,
                "agent": r.agent,
                "model": r.model,
                "mode": r.mode,
                "updated_at": r.updated_at,
                "messages": r.message_count,
                "pinned": r.pinned,
                "archived": r.archived,
                "reasoning_effort": getattr(r, "reasoning_effort", "auto"),
                # §31: non-user origin ("slack") + display label — drives the sidebar's
                # "From Slack" group and the row's platform icon.
                "origin": r.origin,
                "origin_label": r.origin_label,
                # Attention = Inbox items awaiting this session (the amber count that bubbles
                # session → persona → footer Inbox). Liveness = working (in-flight turn) /
                # sleeping (a self-wake is pending) / idle — a count-less dot that never bubbles.
                "attention": len(self.inbox.pending(session_id=r.session_id)),
                "liveness": self._session_liveness(r.session_id),
                # Channels this session listens to (inbound subscriptions) — drives the per-session
                # "connections" indicator.
                "subscriptions": [
                    s.channel for s in self.subscriptions.for_session(r.session_id)
                ],
            }
            for r in self.session_store.list(workspace=ws)
            if not r.session_id.startswith("__")  # hide internal threads
        ]


    def _session_liveness(self, session_id: str) -> str:
        if self.is_running(session_id):
            return "working"
        if self.wakes.pending(session_id):
            return "sleeping"
        return "idle"


    def mark_running(self, session_id: str) -> None:
        self._running_sessions.add(session_id)


    def try_mark_running(self, session_id: str) -> bool:
        """Atomically claim an idle session for one turn on the server event loop."""
        if session_id in self._running_sessions:
            return False
        self._running_sessions.add(session_id)
        return True


    def mark_idle(self, session_id: str) -> None:
        self._running_sessions.discard(session_id)
        # Every turn path (WS, background delivery, durable resume) marks idle when it
        # finishes — the one shared post-turn moment, so auto-titling hooks in here and
        # can never add latency to the response itself.
        self._maybe_autotitle(session_id)


    def is_running(self, session_id: str) -> bool:
        return session_id in self._running_sessions
