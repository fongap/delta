"""Type-declaration surface shared by the SessionManager mixin modules.
SessionManager is composed from ~12 plain mixin classes (see manager.py). Each
mixin reaches into state and helpers that live on the *composed* object — stores,
runtime ports, gateway helpers, autotitle bookkeeping — which a checker cannot see
from the mixin alone. This module declares that shared surface once: class-level
ANNOTATIONS only (no values), so instances gain nothing at runtime and the MRO
still resolves every real definition on the concrete mixins first.

Maintenance: when a mixin starts using a new `self.<member>`, add it here.
Types are deliberately loose (Any / Callable[..., Any]); tighten individual
members as needed."""
from __future__ import annotations
from typing import Any, Callable


class ManagerBase:
    COMPAT_MODELS: Any
    DEFAULT_PDF_MAX_MB: Any
    DEFAULT_PDF_MAX_PAGES: Any
    DEFAULT_SCRATCH_BASE: Any
    DEFAULT_SESSIONS_PEEK: Any
    MAX_BINARY_PREVIEW: Any
    _AUTOTITLE_PROMPT: Any
    _app_event_sequences: Any
    _apply_grants: Any
    _autotitle_attempts: Any
    _autotitle_inflight: Any
    _autotitle_tasks: Any
    _data_base: Any
    _event_clients: Any
    _extra_roots_of: Any
    _mcp_authorizing: Any
    _mcp_errors: Any
    _ollama_alive_cache: Any
    _people: Any
    _people_path: Any
    _prefs: Any
    _running_sessions: Any
    _runtimes: Any
    _session_clients: Any
    _session_event_sequences: Any
    _wake_message: Any
    _workspace_kind: Any
    audit_store: Any
    channel_buffer: Any
    default_workspace: Any
    gateway: Any
    inbox: Any
    inbox_routing: Any
    mcp: Any
    memory_settings: Any
    memory_store: Any
    mention_sessions: Any
    mode: Any
    model: Any
    parked: Any
    persona_connections: Any
    personas: Any
    provider: Any
    run_ledger: Any
    scheduler: Any
    secrets: Any
    session_connections: Any
    session_skills: Any
    session_store: Any
    skill_store: Any
    subscriptions: Any
    task_store: Any
    unattended: Any
    unrouted: Any
    wakes: Any
    workspace_trust: Any

    _artifact_target: Callable[..., Any]
    _bind_runtime: Callable[..., Any]
    _build_and_start_gateway: Callable[..., Any]
    _build_task_engine: Callable[..., Any]
    _connected_connectors: Callable[..., Any]
    _connection_detail: Callable[..., Any]
    _curated_models: Callable[..., Any]
    _dispatch_inbound: Callable[..., Any]
    _durable_resume: Callable[..., Any]
    _emit_session_created: Callable[..., Any]
    _generate_autotitle: Callable[..., Any]
    _has_manual_slack_inbox_binding: Callable[..., Any]
    _inbound_connector_allowed: Callable[..., Any]
    _load_prefs: Callable[..., Any]
    _maybe_autotitle: Callable[..., Any]
    _mcp_workspace_trusted: Callable[..., Any]
    _memory_saved_notifier: Callable[..., Any]
    _model_provider: Callable[..., Any]
    _nav_layout: Callable[..., Any]
    _note_person: Callable[..., Any]
    _note_provider_use: Callable[..., Any]
    _notify_task_done: Callable[..., Any]
    _ollama_alive: Callable[..., Any]
    _ollama_models: Callable[..., Any]
    _on_interaction: Callable[..., Any]
    _park_unauthorized: Callable[..., Any]
    _persona_default_connections: Callable[..., Any]
    _persona_of: Callable[..., Any]
    _prefs_path: Callable[..., Any]
    _provider_configured: Callable[..., Any]
    _provision_scratch: Callable[..., Any]
    _record_process_event: Callable[..., Any]
    _refresh_provider: Callable[..., Any]
    _resolve_inbox_reply: Callable[..., Any]
    _resume_wake: Callable[..., Any]
    _route_mention: Callable[..., Any]
    _routing_targets: Callable[..., Any]
    _run_scheduled_task: Callable[..., Any]
    _save_prefs: Callable[..., Any]
    _scheduled_approver: Callable[..., Any]
    _scratch_workspace_error: Callable[..., Any]
    _seed_task_permissions: Callable[..., Any]
    _session_liveness: Callable[..., Any]
    _set_allowed: Callable[..., Any]
    _slack_actor_owns_item: Callable[..., Any]
    _spawn_mention_session: Callable[..., Any]
    _suggested_models: Callable[..., Any]
    _surfaces: Callable[..., Any]
    add_model: Callable[..., Any]
    add_root: Callable[..., Any]
    approval_outcome: Callable[..., Any]
    approval_prompt_data: Callable[..., Any]
    broadcast_event: Callable[..., Any]
    broadcast_session: Callable[..., Any]
    compaction_settings: Callable[..., Any]
    compaction_settings_payload: Callable[..., Any]
    connect_mcp: Callable[..., Any]
    context_bar: Callable[..., Any]
    deliver_to_session: Callable[..., Any]
    dm_session: Callable[..., Any]
    effective_connectors: Callable[..., Any]
    effective_skill_names: Callable[..., Any]
    engine_workspace: Callable[..., Any]
    get_engine: Callable[..., Any]
    get_roots: Callable[..., Any]
    get_settings: Callable[..., Any]
    inbox_approver: Callable[..., Any]
    inbox_directory_requester: Callable[..., Any]
    inbox_plan_approver: Callable[..., Any]
    inbox_question_asker: Callable[..., Any]
    is_running: Callable[..., Any]
    mark_idle: Callable[..., Any]
    mark_running: Callable[..., Any]
    mint_task_rule: Callable[..., Any]
    mirror_inbox_item: Callable[..., Any]
    pdf_settings: Callable[..., Any]
    persist_session: Callable[..., Any]
    refresh_gateway: Callable[..., Any]
    remove_custom_provider: Callable[..., Any]
    resolve_inbox: Callable[..., Any]
    resolve_workspace: Callable[..., Any]
    resume_due_wakes: Callable[..., Any]
    save: Callable[..., Any]
    scratch_base: Callable[..., Any]
    session_event: Callable[..., Any]
    sessions_peek: Callable[..., Any]
    set_default_model: Callable[..., Any]
    set_provider: Callable[..., Any]
    slack_approval_owner_ids: Callable[..., Any]
    stop_gateway: Callable[..., Any]
    try_mark_running: Callable[..., Any]
    unregister_event_client: Callable[..., Any]
    unregister_session_client: Callable[..., Any]
    workspace_command_trust: Callable[..., Any]
