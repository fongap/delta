"""Static host contract shared by the SessionManager mixins.

At runtime this base is intentionally empty, so it does not add behavior or compete with
the cooperative mixin MRO. Type checkers see the complete host state and cross-mixin method
surface assembled by :class:`services.server.manager.SessionManager`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path
    from typing import Any, Awaitable, Callable

    from core.audit import AuditStore
    from core.automation import Scheduler, TaskStore
    from core.connections import PersonaConnectionStore, SessionConnectionStore
    from core.conversations import ConversationStore
    from core.engine import Approver, TurnEngine
    from core.idemlog import IdempotencyLog
    from core.inbox import InboxStore
    from core.inbox_routing import InboxRouting
    from core.ledger import RunEventLedger
    from core.memory import MemorySettingsStore, MemoryStore
    from core.mentions import MentionSessionStore
    from core.permissions import Mode
    from core.personas import PersonaRegistry
    from core.runtime import RuntimePort
    from core.selfwake import WakeStore
    from core.subscriptions import ChannelBuffer, SubscriptionStore
    from core.unattended import UnattendedRegistry
    from core.unrouted import UnroutedStore
    from core.workspace_trust import WorkspaceTrustStore
    from integrations.connectors import Gateway
    from integrations.connectors.parked import ParkedStore
    from integrations.mcp import MCPManager
    from integrations.skills import SessionSkillStore, SkillStore
    from packages.secrets import SecretStore
    from providers import ProviderClient

    EventSender = Callable[[dict[str, Any]], Awaitable[None]]


    class ManagerHostState:
        """State and cross-mixin operations supplied by the concrete SessionManager."""

        DEFAULT_SCRATCH_BASE: str

        default_workspace: str | None
        model: str
        mode: Mode
        provider: ProviderClient
        memory_store: MemoryStore
        memory_settings: MemorySettingsStore
        audit_store: AuditStore
        run_ledger: RunEventLedger
        idem_log: IdempotencyLog
        recovery_store: Any  # core.recovery.RecoveryStore
        audit_sink: Callable[[dict[str, Any]], None]
        session_store: ConversationStore
        workspace_trust: WorkspaceTrustStore
        secrets: SecretStore
        mcp: MCPManager
        gateway: Gateway | None
        task_store: TaskStore
        scheduler: Scheduler
        personas: PersonaRegistry
        inbox: InboxStore
        inbox_routing: InboxRouting
        unattended: UnattendedRegistry
        wakes: WakeStore
        subscriptions: SubscriptionStore
        channel_buffer: ChannelBuffer
        mention_sessions: MentionSessionStore
        parked: ParkedStore
        persona_connections: PersonaConnectionStore
        session_connections: SessionConnectionStore
        skill_store: SkillStore
        session_skills: SessionSkillStore
        unrouted: UnroutedStore

        _data_base: Path
        _prefs: dict[str, Any]
        _runtimes: dict[str, RuntimePort]
        _running_sessions: set[str]
        _autotitle_inflight: set[str]
        _autotitle_tasks: set[asyncio.Task[Any]]
        _autotitle_attempts: dict[str, int]
        _mcp_authorizing: set[str]
        _mcp_errors: dict[str, str]
        _people_path: Path
        _people: dict[str, str]
        _session_clients: dict[str, set[EventSender]]
        _session_event_sequences: dict[str, int]
        _app_event_sequences: dict[str | None, int]
        _event_clients: set[EventSender]

        def _bind_runtime(self, engine: TurnEngine, session_id: str, *, run_id: str | None = None) -> RuntimePort: ...
        def _emit_session_created(self, session_id: str, persona_id: str) -> None: ...
        @staticmethod
        def _extra_roots_of(runtime: RuntimePort) -> list[dict[str, Any]]: ...
        def _inbound_connector_allowed(self, session_id: str, connector: str) -> bool: ...
        def _mcp_workspace_trusted(self, workspace: str | Path | None) -> bool: ...
        def _memory_saved_notifier(self, session_id: str) -> Any: ...
        def _note_person(
            self, platform: str, user_id: str | None, name: str | None
        ) -> None: ...
        def _provision_scratch(self, session_id: str) -> str: ...
        def _routing_targets(self, session_id: str, agent: str) -> list[str]: ...
        def _save_prefs(self) -> None: ...
        def _scratch_workspace_error(
            self, workspace: Any
        ) -> dict[str, Any] | None: ...
        def _seed_task_permissions(self, runtime: RuntimePort, task: Any) -> None: ...
        def _set_allowed(
            self,
            name: str,
            user_id: str,
            *,
            team_id: str | None = None,
            add: bool,
        ) -> dict[str, Any]: ...
        def _slack_actor_owns_item(
            self,
            item: Any,
            *,
            actor_id: str,
            chat_id: str,
            team_id: str | None,
        ) -> bool: ...
        def add_root(
            self, session_id: str, path: str, writable: bool = False
        ) -> dict[str, Any]: ...
        def approval_outcome(
            self, resolution: str, request: Any, session_id: str
        ) -> Any: ...
        def approval_prompt_data(
            self, session_id: str, request: Any
        ) -> dict[str, Any]: ...
        async def broadcast_event(
            self,
            event_type: str,
            session_id: str | None,
            payload: dict[str, Any],
        ) -> None: ...
        async def broadcast_session(
            self, session_id: str, event_type: str, payload: dict[str, Any]
        ) -> None: ...
        def compaction_settings(self) -> dict[str, Any]: ...
        def dm_session(self) -> str | None: ...
        def effective_connectors(
            self, session_id: str, persona_id: str | None = None
        ) -> set[str]: ...
        def effective_skill_names(
            self, session_id: str, workspace: str | Path | None = None
        ) -> set[str]: ...
        def engine_workspace(
            self,
            session_id: str,
            *,
            workspace: str | None = None,
            agent: str = "code",
        ) -> str | None: ...
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
        ) -> RuntimePort | None: ...
        def get_settings(self) -> dict[str, Any]: ...
        def inbox_approver(self, session_id: str, agent: str) -> Any: ...
        def inbox_directory_requester(self, session_id: str, agent: str) -> Any: ...
        def inbox_plan_approver(self, session_id: str, agent: str) -> Any: ...
        def inbox_question_asker(self, session_id: str, agent: str) -> Any: ...
        def is_running(self, session_id: str) -> bool: ...
        def mark_idle(self, session_id: str) -> None: ...
        def mark_running(self, session_id: str) -> None: ...
        async def mirror_inbox_item(self, item: Any) -> None: ...
        def persist_session(self, session_id: str) -> None: ...
        async def refresh_gateway(self) -> list[str]: ...
        async def resolve_inbox(self, item_id: str, resolution: str) -> bool: ...
        def resolve_workspace(self, requested: str | None) -> str | None: ...
        def save(self, session_id: str, runtime: RuntimePort) -> None: ...
        def scratch_base(self) -> Path: ...
        def slack_approval_owner_ids(self, team_id: str | None = None) -> set[str]: ...
        def try_mark_running(self, session_id: str) -> bool: ...

else:

    class ManagerHostState:
        """Runtime marker base; the concrete mixins provide all behavior."""

        pass
