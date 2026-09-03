"""Session manager — owns engines (one per session), stores, and the provider.

Each session is bound to a workspace folder (Code requires one). Storage is a single DB
under a data dir (global for the real server, per-workspace for tests), so recents and
sessions span folders.
"""

from __future__ import annotations

import asyncio
import json
import os  # noqa: F401 — re-exported for tests that patch services.server.manager.os
from pathlib import Path
from typing import Any

from core.audit import AuditStore
from core.automation import Scheduler, TaskStore
from core.connections import (
    PersonaConnectionStore,
    SessionConnectionStore,
)
from integrations.connectors import (
    Gateway,
)
from integrations.connectors.parked import ParkedStore
from core.conversations import ConversationStore
from core.inbox import InboxStore
from core.inbox_routing import InboxRouting
from core.ledger import RunEventLedger
from integrations.mcp import (
    MCPManager,
)
from core.memory import MemorySettingsStore, MemoryStore, SQLiteMemoryStore
from core.mentions import MentionSessionStore
from core.permissions import Mode
from core.personas import PersonaRegistry
from core.personas.registry import set_registry as set_persona_registry
from providers import (
    ProviderClient,
    ProviderRouter,
    fetch_provider_models,
    get_descriptor,
    migrate_legacy_provider_profiles,
    provider_profile_key,
    register_custom_provider,
)
from core.runtime import RuntimePort
from packages.secrets import SecretStore, state_dir
from core.selfwake import WakeStore
from integrations.skills import (
    SessionSkillStore,
    SkillStore,
)
from core.subscriptions import ChannelBuffer, SubscriptionStore
from core.unattended import UnattendedRegistry
from core.unrouted import UnroutedStore
from core.workspace_trust import WorkspaceTrustStore
from services.server.manager_artifacts import ArtifactsBrowserAuditMixin
from services.server.manager_automations import AutomationsMixin
from services.server.manager_connections import ConnectionsMixin
from services.server.manager_events import EventsMixin
from services.server.manager_gateway import GatewayInboundMixin
from services.server.manager_inbox import InboxApprovalsMixin
from services.server.manager_mcp_connectors import McpConnectorsMixin
from services.server.manager_providers import ProvidersSettingsMixin
from services.server.manager_sessions import SessionsMixin
from services.server.manager_skills_memory import SkillsMemoryMixin

# Re-exported helpers the rest of the application layer imports from the manager
# package (kept here for backward-compatible import paths; see tests + mixins).
from services.server.manager_workspace import WorkspaceTrustMixin


class SessionManager(
    WorkspaceTrustMixin,
    SessionsMixin,
    EventsMixin,
    McpConnectorsMixin,
    ConnectionsMixin,
    InboxApprovalsMixin,
    GatewayInboundMixin,
    AutomationsMixin,
    ArtifactsBrowserAuditMixin,
    ProvidersSettingsMixin,
    SkillsMemoryMixin,
):
    def __init__(
        self,
        *,
        workspace: str | Path | None = None,  # default/seed workspace (e.g. --cwd)
        data_dir: str | Path | None = None,
        model: str = "",
        mode: Mode = Mode.INTERACTIVE,
        provider: ProviderClient | None = None,
    ) -> None:
        self.default_workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace else None
        )
        self.model = model
        self.mode = mode
        self.provider: ProviderClient

        if data_dir is not None:
            base = Path(data_dir).expanduser()
        elif self.default_workspace is not None:
            base = Path(self.default_workspace) / ".delta"
        else:
            base = state_dir()
        base.mkdir(parents=True, exist_ok=True)

        self.memory_store: MemoryStore = SQLiteMemoryStore(base / "core.db")
        # MEMORY-SPEC §4.3/§6: the on/off switch + the user's standing rules. Settings-
        # level, outside the memory table; read at engine build time.
        self.memory_settings = MemorySettingsStore(base / "memory-settings.json")
        self.audit_store = AuditStore(base / "core.db")
        self.run_ledger = RunEventLedger(base / "run-events.db")
        # ADR-005 WS4: durable dedupe of side effects. Survives a crash so
        # resume() can tell "this call's effect already happened" from "this
        # call is new". Lives next to the run ledger.
        from core.idemlog import IdempotencyLog

        self.idem_log = IdempotencyLog(base / "side-effects.db")
        # ADR-005: collapse tool/approval facts into the run ledger. The audit sink
        # still writes to AuditStore for backward compatibility; the mirroring
        # helper additionally appends a ledger event when an ambient run scope
        # names the owning run.
        from core.ledger_event import make_mirroring_audit_sink

        self.audit_sink = make_mirroring_audit_sink(
            self.audit_store.append,
            ledger_append=self.run_ledger.append,
        )
        self.session_store = ConversationStore(base)
        self.session_store.canonicalize_workspaces()  # collapse /tmp vs /private/tmp etc.
        if self.default_workspace:
            self.session_store.touch_workspace(self.default_workspace)
        self._runtimes: dict[str, RuntimePort] = {}
        self._running_sessions: set[str] = (
            set()
        )  # sessions with an in-flight turn (busy)
        # Sessions with an auto-title LLM call in flight (FB-010) — one call at a time.
        self._autotitle_inflight: set[str] = set()
        self._autotitle_tasks: set[asyncio.Task] = set()
        self._autotitle_attempts: dict[str, int] = {}
        self.workspace_trust = WorkspaceTrustStore()
        self.secrets = SecretStore()
        # No explicit provider injected → route by the model's `provider:` prefix (OpenAI default,
        # custom aliases, …). Tests inject a provider directly and bypass the router. The same router is
        # shared by every engine and the `/v1/chat/completions` proxy.
        if provider is None:
            self.provider = ProviderRouter(
                self.secrets, default_provider="openai", on_use=self._note_provider_use
            )
        else:
            self.provider = provider
        self.mcp = MCPManager(secrets=self.secrets)
        # OAuth MCP servers with a sign-in in flight / their last connect error —
        # feeds list_mcp's status so the GUI can show "authorizing…" and failures.
        self._mcp_authorizing: set[str] = set()
        self._mcp_errors: dict[str, str] = {}
        self.gateway: Gateway | None = None
        self._data_base = base
        # P2 实用 (DELTA_BLUEPRINT §7.2): per-workspace Source ledgers, kept
        # alongside the run ledger so sources follow the workspace. LRU-ish —
        # bounded by the number of workspaces the user has touched this
        # session; eviction is on process restart, which is fine because the
        # state is reloaded from disk on next access.
        self._source_stores: dict[str, Any] = {}
        # Desktop/UI prefs (default model, onboarding state) — not secrets; a plain JSON file.
        self._prefs = self._load_prefs()
        migration = migrate_legacy_provider_profiles(self.secrets, self._prefs)
        if migration.get("migrated") or migration.get("duplicates_removed") or not self._prefs_path().exists():
            self._save_prefs()
        if self._prefs.get("default_model"):
            self.model = self._prefs["default_model"]
        # Re-hydrate user-created profiles (alias -> protocol) so `alias:model` remains
        # compatible while the router itself dispatches by protocol, not vendor.
        for alias, meta in (self._prefs.get("provider_profiles") or {}).items():
            try:
                register_custom_provider(alias, meta["protocol"], meta)
            except (ValueError, KeyError):
                # A stale/legacy entry must not kill startup — skip only that alias.
                continue
        # Seed the PDF-fallback module global from prefs so engines see the user's
        # choice from the first turn (set_pdf_settings keeps it in sync after).
        from core.pdf_support import set_fallback_mode

        set_fallback_mode(self.pdf_settings()["pdf_fallback"])
        # Per-session live-view registry: every socket open on a session id gets the turn's events,
        # whoever drives the turn (foreground user_message, channel delivery, self-wake, resume).
        # Delivery itself is socket-independent — this only governs *live visibility*.
        self._session_clients: dict[str, set[Any]] = {}
        self._session_event_sequences: dict[str, int] = {}
        self._app_event_sequences: dict[str | None, int] = {}
        # App-wide event sockets (/ws/events): session-independent pushes — today the
        # automation-run-started toast (UX-026); badges could ride it later.
        self._event_clients: set[Any] = set()
        # Automation: scheduled tasks store + the tick scheduler (started in the lifespan).
        # The scheduler also resumes self-wake'd sessions each tick (extra_tick).
        self.task_store = TaskStore(base / "automation.db")
        self.scheduler = Scheduler(
            self.task_store, self._run_scheduled_task, extra_tick=self.resume_due_wakes
        )
        # Personas: registry + lifecycle state under this manager's data dir. Installed as the
        # process singleton so agents.get_agent resolves persona ids (incl. third-party) here.
        self.personas = PersonaRegistry(state_path=base / "personas.json")
        set_persona_registry(self.personas)
        # Inbox (cross-session human-attention queue), routing (named inboxes + Slack/Telegram
        # bindings), the Unattended toggle, and self-wake records.
        self.inbox = InboxStore(base / "inbox.json")
        self.inbox_routing = InboxRouting(base / "inbox_routing.json")
        self.unattended = UnattendedRegistry(base / "unattended.json")
        self.wakes = WakeStore(base / "wakes.json")
        # Channel subscriptions (inbound): persisted (session_id, channel) records + a ring buffer
        # of recently-seen channel messages for get_channel_messages.
        self.subscriptions = SubscriptionStore(base / "subscriptions.json")
        self.channel_buffer = ChannelBuffer(state_path=base / "channels.json")
        # Mention router (§31): thread target → the session that owns that Slack thread.
        # Also the durable source of the thread's standing send_message grant (re-seeded
        # onto the engine in get_engine).
        self.mention_sessions = MentionSessionStore(base / "mention_threads.json")
        # Unauthorized inbound messages, parked instead of dropped (one-step allow-and-deliver).
        self.parked = ParkedStore(base / "parked.json")
        # People directory: "platform:user_id" → display name, noted from every inbound
        # (authorized or parked) so allow-list chips read "Rohit Prsad", not "U07JK…".
        self._people_path = base / "people.json"
        try:
            self._people: dict[str, str] = json.loads(self._people_path.read_text())
        except (OSError, ValueError):
            self._people = {}
        # Seed from already-parked messages (they carry resolved names) so an allow made from
        # an old parked item still gets a named chip.
        for it in self.parked.list():
            if it.get("user_name"):
                self._people.setdefault(
                    f"{it['platform']}:{it['user_id']}", it["user_name"]
                )
        # Connection hierarchy (UI-REFRESH §4): per-persona default connector on/off (seeded from the
        # manifest, then user-editable) + per-session overrides. Resolved into the session's effective
        # connector set, which gates inbound delivery and the engine's connector tools.
        self.persona_connections = PersonaConnectionStore(
            base / "persona_connections.json"
        )
        self.session_connections = SessionConnectionStore(
            base / "session_connections.json"
        )
        # Skills (SKILLS-SPEC §4): folder-backed CRUD + per-session mutes. The effective menu
        # gates the engine's skill catalog the same way effective_connectors gates connector
        # tools — one resolver feeds the catalog injection, the rail, and the composer popup.
        self.skill_store = SkillStore()
        self.session_skills = SessionSkillStore(base / "session_skills.json")
        # Dead-letter: inbound messages with no destination + background-turn failures, so neither
        # vanishes silently (a debugging/visibility surface, not a redelivery queue).
        self.unrouted = UnroutedStore(base / "unrouted.json")


    async def aclose(self) -> None:
        await self.scheduler.stop()
        await self.stop_gateway()
        await self.mcp.aclose()
        for runtime in self._runtimes.values():
            # App shutdown kills each session's managed background tasks;
            # detached (detach=true) tasks survive by design.
            runtime.shutdown_executor()
        self.audit_store.close()


    def source_store_for(self, workspace: str | None, *, run_id: str | None) -> Any | None:
        """P2 实用 — return the per-workspace :class:`core.sources.SourceStore`,
        creating + caching it on first use.

        The store lives at ``<workspace>/.delta/sources.json`` (parallel to
        ``run-events.db`` and ``side-effects.db``), so the run ledger and the
        source ledger are restored together. Returns None when no workspace
        is bound (chat sessions) — readers fall back to no-op citations.

        ``run_id`` is not used to key the store (sources are per-workspace,
        not per-run — a ref survives the run that captured it). It's part
        of the signature so the caller can pass it through the same kwargs
        the engine sees, with no special-casing.
        """
        del run_id  # signature symmetry only
        if not workspace:
            return None
        cached = self._source_stores.get(workspace)
        if cached is not None:
            return cached
        from core.sources import SourceStore

        ws_path = Path(workspace).expanduser()
        base = ws_path / ".delta"
        base.mkdir(parents=True, exist_ok=True)
        store = SourceStore(base / "sources.json", workspace=ws_path)
        self._source_stores[workspace] = store
        return store

    def fetch_models(
        self, alias: str, fields: dict[str, Any] | None, timeout: float = 10.0
    ) -> dict[str, Any]:
        """Fetch the model list for a custom provider alias, then auto-add each id by prefix
        as `alias:{model_id}` (idempotent — already-present ids are skipped)."""
        d = get_descriptor(alias)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {alias}"}
        # Probe with the supplied fields merged over any stored profile, so a never-saved
        # form submission can still test before persisting.
        profile = dict(self.secrets.get(provider_profile_key(alias)) or {})
        merged: dict[str, Any] = {}
        for f in d.fields:
            val = (fields or {}).get(f.key) or profile.get(f.key) or ""
            if isinstance(val, str):
                val = val.strip()
            if val:
                merged[f.key] = val
        merged["protocol"] = profile.get("protocol") or d.protocol
        result = fetch_provider_models(alias, merged, self.secrets, timeout)
        if not result.get("ok"):
            return result
        added: list[str] = []
        for mid in result.get("models", []):
            model = f"{alias}:{mid}"
            existing = self._prefs.get("models") or []
            if model not in existing:
                self.add_model(model)
                added.append(model)
        return {"ok": True, "alias": alias, "models": result.get("models", []), "added": added}
