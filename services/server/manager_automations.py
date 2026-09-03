"""Scheduled-task automation runs: task engines, execution, CRUD, manual runs.

Extracted verbatim from SessionManager (see manager.py); composed back via
mixin inheritance so behavior is unchanged.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from core.agent import build_engine
from core.agents import get_agent
from core.automation import Schedule, ScheduledTask, TaskRun
from core.permissions import Mode
from core.runtime import RuntimePort
from services.server.manager_support import _approval_body, _epoch, _last_assistant_text


from services.server.manager_contract import ManagerHostState


class AutomationsMixin(ManagerHostState):

    def _scheduled_approver(self, task, session_id: str):
        from core.engine import ApprovalOutcome
        from core.permissions import WRITE_TOOLS

        name_allowed = task.name_allowed_tools()

        async def approver(request):
            # Unattended: auto-allow the deliverable writes (path-scoped to the task
            # workspace) + tools the task allows BY NAME (legacy entries). Target-bound
            # rules never reach here — the permission engine matched them already.
            if request.tool_name in WRITE_TOOLS or request.tool_name in name_allowed:
                return ApprovalOutcome.ONCE
            # Anything else parks in the Inbox and suspends the run (§25 graceful
            # degradation — an ungranted automation still works, it just asks). The item
            # carries the task binding so the in-app card can offer "Allow every time";
            # the Slack mirror renders only Approve/Deny buttons.
            item = self.inbox.add_approval(
                session_id,
                f"Run `{request.tool_name}`?",
                body=_approval_body(request),
                inbox=self.inbox_routing.route_for(session_id, task.agent),
                tool_call_id=getattr(request, "tool_call_id", None),
                data=self.approval_prompt_data(session_id, request),
            )
            if item.state == "pending":
                self.persist_session(session_id)
                await self.mirror_inbox_item(item)
            resolution = await self.inbox.wait(item.id)
            return self.approval_outcome(resolution, request, session_id)

        return approver


    def _seed_task_permissions(self, runtime: RuntimePort, task) -> None:
        """Apply a task's standing allowances to a runtime: target-bound rules feed the
        permission engine's matcher (connector tools included — the target binding is the
        safety); name-only legacy entries keep their session-allowlist behavior."""
        runtime.set_task_rules(task.standing_rules())
        for tool in task.name_allowed_tools():
            runtime.grant_tool(tool)


    def _build_task_engine(self, task, *, session_id: str, run_id: str | None = None) -> RuntimePort:
        ag = get_agent(task.agent)
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        engine = build_engine(
            agent=ag,
            workspace=task.workspace,
            model=task.model or self.model,
            mode=Mode.INTERACTIVE,
            approver=self._scheduled_approver(task, session_id),
            provider=self.provider,
            memory_store=self.memory_store,
            memory_off=not self.memory_settings.enabled,
            memory_saving_enabled=lambda: self.memory_settings.enabled,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open (same reason as the saving switch).
            user_rules=lambda: self.memory_settings.user_rules,
            on_memory_saved=self._memory_saved_notifier(session_id),
            secrets=self.secrets,
            # No scheduling tools inside a scheduled run: the executing agent's job is to DO the
            # task, and instructions that mention timing ("every day at 5:32pm…") otherwise tempt
            # it to create another automation instead of running this one.
            task_store=None,
            session_id=session_id,
            audit_sink=self.audit_sink,
            # Scheduled runs respect the same per-session connection hierarchy as live sessions:
            # expose only the persona's effective-enabled connectors' tools (§4.3).
            connector_filter=self.effective_connectors(session_id, task.agent),
            skill_filter=lambda sid=session_id, w=task.workspace: (
                self.effective_skill_names(sid, w)
            ),
            # ADR-005 WS4: scheduled tasks are exactly where the
            # crash-after-side-effect window matters (long unattended runs).
            idem_log=self.idem_log,
            # P2 实用: the source ledger flows into readers so every
            # successful read auto-cites the run with a typed locator. The
            # store lives in the same per-workspace data dir as the run
            # artifacts (workspaces can mix-and-match without colliding).
            # ``run_id`` is the G1 single identity (ADR-005): the same id
            # the TaskRun / ledger / artifact / idemlog already share, so
            # the citation joins the same run trail as the rest.
            source_store=self.source_store_for(task.workspace, run_id=run_id),  # type: ignore[attr-defined]
            run_id=run_id,
        )
        runtime = self._bind_runtime(engine, session_id, run_id=run_id)
        self._seed_task_permissions(runtime, task)
        return runtime


    async def _run_scheduled_task(self, task, trigger: str) -> TaskRun:
        run = TaskRun(
            task_id=task.id, trigger=trigger, workspace=task.workspace or ""
        )  # __post_init__ sets run.session_id
        self.task_store.add_run(run)  # mark "running"
        # UX-026: tell every open app window a SCHEDULED run just started (the 5s
        # top-right toast). Manual runs never come through here — the user is
        # already watching those live.
        await self.broadcast_event(
            "automation_run_started",
            run.session_id,
            {
                "task_id": task.id,
                "task_title": task.title,
                "session_id": run.session_id,
                "workspace": task.workspace,
                "agent": task.agent,
                "trigger": trigger,
            },
        )
        # Each run is a real, persisted conversation thread: it runs the instructions under its
        # own session id, then saves the transcript. The user can reopen that session and ask a
        # follow-up — the scheduled agent is no longer fire-and-forget.
        runtime = self._build_task_engine(
            task, session_id=run.session_id, run_id=run.run_id
        )
        # Register the live runtime up-front: a parked approval persists the session
        # mid-run (durable suspend), and resolving from the Inbox must find this engine.
        # The adapter (built with the run ledger) makes this a durable, ledgered run.
        self._runtimes[run.session_id] = runtime
        # The first turn is the task itself. The framing matters: instructions often restate the
        # schedule ("every day at 5:32pm…"), so make explicit that the schedule already fired and
        # the job now is to execute, not to (re)schedule.
        opening = (
            f"⏰ Scheduled run — {task.title}\n\n"
            "This automation is due now: carry out the task below immediately and produce the "
            "result. The schedule already exists — do not create or modify any scheduled tasks.\n\n"
            f"{task.instructions}"
        )
        try:
            async for _event in runtime.run(opening):
                pass
            run.result_text = _last_assistant_text(runtime.messages)
            from core.artifact import register_run_artifacts

            artifacts = register_run_artifacts(
                task.workspace,
                run_id=run.run_id,
                since=run.started_at,
                ledger=self.run_ledger,
            )
            run.artifacts = [a.to_dict() for a in artifacts]
            # ADR-005 WS3: deterministic validation gate. The engine returning
            # without raising is necessary but NOT sufficient — the result must
            # also satisfy the task's validation_criteria (defaults to the safe
            # floor: at least one artifact, all complete). Validation runs
            # before notify so a validation failure does not look "done".
            run.status = self._validate_run(run, task, artifacts)
            if run.status == "ok" and task.notify_on_completion:
                await self._notify_task_done(task, run)
        except Exception as exc:
            run.status, run.error = "error", str(exc)
        finally:
            run.finished_at = _epoch()
            # Persist the run as a continuable session + keep the live runtime for an
            # immediate follow-up; record the run (now carrying its session_id).
            try:
                self.save(run.session_id, runtime)
                self._runtimes[run.session_id] = runtime
            except Exception:
                pass
            self.task_store.add_run(run)
            # §7.2 Inbox 收敛: a run that did NOT succeed (error /
            # validation_failed / skipped) surfaces as an issue in the
            # unified Inbox queue — not silent. The user can acknowledge
            # it there (or reopen the run's own continuable session).
            if run.status != "ok":
                await self._notify_task_issue(task, run)
        return run

    async def _notify_task_issue(self, task, run: TaskRun) -> None:
        """Create an Inbox run_issue item for a non-ok scheduled run.

        Fills the card with the run + task binding and the error/
        status text so the user can see at a glance what failed and
        where to resume. Idempotent per run_id (a re-drive won't stack
        duplicates); the item resolves when the user acknowledges it.
        """
        status_label = {
            "error": "errored",
            "validation_failed": "failed validation",
            "skipped": "was skipped",
        }.get(run.status, run.status)
        title = f"Automation '{task.title}' {status_label}"
        detail = (run.error or "").strip()
        body = (
            f"Task: {task.title} (id {task.id})\n"
            f"Run: {run.run_id} · status {run.status}\n"
        )
        if detail:
            body += f"\n{detail[:400]}"
        try:
            await self.mirror_inbox_item(
                self.inbox.add_run_issue(
                    run.session_id,
                    title,
                    body=body,
                    inbox=self.inbox_routing.route_for(run.session_id, task.agent),
                    data={
                        "run_id": run.run_id,
                        "task_id": task.id,
                        "task_title": task.title,
                        "status": run.status,
                        "error": run.error,
                    },
                )
            )
        except Exception:
            # Never let the notification path tear down the run finalize.
            pass


    def _validate_run(
        self,
        run: TaskRun,
        task,
        artifacts: list,
    ) -> str:
        """Run the task's validation criteria against the produced artifacts.

        The engine succeeded (`run.error is None` at the call site) — this
        returns one of `"ok"`, `"validation_failed"`. The criterion check is
        deterministic (artifact count, paths, size, substrings, CSV headers)
        and is recorded into the run ledger so the verdict is replayable.
        """
        from core.validation import (
            DEFAULT_CRITERIA,
            ValidationCriteria,
            gate_status,
            run_validation,
        )

        criteria_dict = task.validation_criteria
        if criteria_dict is None:
            criteria = DEFAULT_CRITERIA
        else:
            criteria = ValidationCriteria.from_dict(criteria_dict)

        result = run_validation(artifacts, criteria, workspace=task.workspace)
        # Ledger the verdict so a follow-up can replay the gate decision.
        try:
            self.run_ledger.append(
                run.run_id,
                "validation.passed" if result.ok else "validation.failed",
                actor="system",
                payload=result.to_dict(),
                workspace=run.workspace or (task.workspace or None),
            )
        except Exception:
            pass
        return gate_status(result, engine_succeeded=True)


    async def _notify_task_done(self, task, run: TaskRun) -> None:
        summary = (run.result_text or "").strip()[:280]
        # Notify any socket viewing this scheduled run's session (it's a durable session of its own).
        await self.broadcast_session(
            run.session_id,
            "task_done",
            {
                "task": task.title,
                "id": task.id,
                "text": summary,
                "run_id": run.run_id,
            },
        )
        if task.notify_target:
            from integrations.connectors.base import parse_target
            from integrations.connectors.senders import DEFAULT_SENDERS

            try:
                platform, chat_id, thread = parse_target(task.notify_target)
                sender = DEFAULT_SENDERS.get(platform)
                creds = self.secrets.get(f"{platform}:default") or {}
                if sender and creds.get("bot_token"):
                    await asyncio.to_thread(
                        sender,
                        creds["bot_token"],
                        chat_id,
                        f"✓ {task.title}\n\n{summary}",
                        thread,
                    )
            except Exception:
                pass


    # -- automation REST --------------------------------------------------------
    def list_automations(self) -> dict[str, Any]:
        # Unseen = runs started after the task's seen mark (UX-023 sidebar badges).
        # `unseen_failed` tints the badge when the NEWEST unseen run errored.
        tasks = []
        for t in self.task_store.list():
            unseen = [
                r for r in self.task_store.runs(t.id) if r.started_at > t.seen_runs_at
            ]
            tasks.append(
                {
                    **t.public(),
                    "unseen_runs": len(unseen),
                    "unseen_failed": bool(unseen) and unseen[0].status == "error",
                }
            )
        return {"tasks": tasks}


    def mark_automation_seen(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        task.seen_runs_at = time.time()
        self.task_store.save(task)
        return {"ok": True}


    def get_automation(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"error": "not found"}
        return {
            "task": task.public(),
            "runs": [r.to_dict() for r in self.task_store.runs(task_id)],
        }


    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an automation directly from the GUI (the "New automation" / template flow).
        Mirrors the agent-facing `create_scheduled_task` validation, but binds the task to a
        fresh per-task scratch workspace instead of an origin conversation's folder."""
        from croniter import croniter

        title = (payload.get("title") or "").strip()
        instructions = (payload.get("instructions") or "").strip()
        cron = (payload.get("cron") or "").strip() or None
        fire_at = (payload.get("fire_at") or "").strip() or None
        timezone = (payload.get("timezone") or "").strip() or "local"

        if not title:
            return {"ok": False, "error": "title is required"}
        if not instructions:
            return {"ok": False, "error": "instructions are required"}
        if not cron and not fire_at:
            return {
                "ok": False,
                "error": "provide a cron (recurring) or a fire_at ISO datetime (one-time)",
            }
        if cron and not croniter.is_valid(cron):
            return {"ok": False, "error": f"invalid cron expression: {cron}"}

        schedule = Schedule(
            kind="once" if (fire_at and not cron) else "cron",
            cron=cron,
            fire_at=fire_at,
            timezone=timezone,
        )
        from core.automation.models import grant_entries

        task = ScheduledTask(
            title=title,
            instructions=instructions,
            schedule=schedule,
            workspace="",
            origin_surface="cowork",
            agent="cowork",
            # Human-driven path (GUI form / onboarding recipes): the creating surface
            # rendered the grants, the submit IS the consent. Same validation as the
            # agent tool — only target-bound write grants survive.
            always_allowed_tools=grant_entries(payload.get("permissions")),
        )
        task.workspace = self._provision_scratch(task.task_session_id)
        self.task_store.save(task)
        return {"ok": True, "task": task.public()}


    def update_automation(
        self, task_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        if "enabled" in changes:
            task.enabled = bool(changes["enabled"])
        if changes.get("instructions") is not None:
            task.instructions = changes["instructions"]
        if changes.get("title") is not None:
            task.title = changes["title"]
        if changes.get("cron") is not None:
            from croniter import croniter

            if not croniter.is_valid(changes["cron"]):
                return {"ok": False, "error": "invalid cron"}
            task.schedule.cron, task.schedule.kind = changes["cron"], "cron"
        if changes.get("revoke"):
            # Revocation from the task detail page ("Allowed without asking … · Revoke").
            # Human-only, like minting; the agent-facing update tool has no such field.
            task.revoke_rule(str(changes["revoke"]))
        self.task_store.save(task)
        if changes.get("revoke"):
            # A live run engine may still hold the revoked rule — reseed from the record.
            for sid, runtime in self._runtimes.items():
                owner = self.task_store.task_for_run_session(sid)
                if owner is not None and owner.id == task.id:
                    runtime.set_task_rules(task.standing_rules())
        return {"ok": True, "task": task.public()}


    def delete_automation(self, task_id: str) -> dict[str, Any]:
        return {"ok": self.task_store.delete(task_id), "id": task_id}


    def prepare_manual_run(self, task_id: str) -> dict[str, Any]:
        """Create a 'running' manual run and return its session, so the GUI can open it and
        drive the task LIVE over the normal session WS (you watch the agent + follow up). The
        automatic scheduler path stays headless (`_run_scheduled_task`)."""
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        run = TaskRun(
            task_id=task.id, trigger="manual", workspace=task.workspace or ""
        )  # status "running", session_id auto
        self.task_store.add_run(run)
        return {
            "ok": True,
            "run_id": run.run_id,
            "session_id": run.session_id,
            "workspace": task.workspace,
            "agent": task.agent,
            # Same execute-now framing as the headless path — manual runs ride a normal live
            # session whose engine DOES have scheduling tools, so be explicit.
            "prompt": (
                f"⏰ Running automation '{task.title}' now. Carry out these instructions "
                "immediately and produce the result. The schedule already exists — do not create "
                f"or modify any scheduled tasks.\n\n{task.instructions}"
            ),
        }


    def finalize_manual_run(self, task_id: str, run_id: str) -> dict[str, Any]:
        """Mark a manual run complete once its first turn finished (the WS already saved the
        session). Pulls result text + artifacts from the persisted transcript/workspace.
        """
        run = next(
            (r for r in self.task_store.runs(task_id) if r.run_id == run_id), None
        )
        task = self.task_store.get(task_id)
        if run is None or task is None:
            return {"ok": False, "error": "not found"}
        # Backfill the denormalized workspace column on rows written
        # before the ADR-007 migration landed — every catchup / manual
        # finalize gives us a chance to upgrade the row in place.
        if not run.workspace and task.workspace:
            run.workspace = task.workspace
        if run.status == "running":
            record = self.session_store.load(run.session_id)
            run.result_text = _last_assistant_text(record.messages) if record else None
            from core.artifact import register_run_artifacts

            artifacts = register_run_artifacts(
                task.workspace,
                run_id=run.run_id,
                since=run.started_at,
                ledger=self.run_ledger,
            )
            run.artifacts = [a.to_dict() for a in artifacts]
            run.status = "ok"
            run.finished_at = _epoch()
            self.task_store.add_run(run)
            task.last_run, task.last_status = run.finished_at, "ok"
            task.run_count += 1
            self.task_store.save(task)
        return {"ok": True, "run": run.to_dict()}
