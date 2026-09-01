"""Event/session-client registries and broadcast fan-out.

Extracted verbatim from SessionManager (see manager.py); composed back via
mixin inheritance so behavior is unchanged.
"""

from __future__ import annotations

from typing import Any

from services.server.contracts import runtime_event_v1


from services.server.manager_contract import ManagerHostState


class EventsMixin(ManagerHostState):

    def _emit_session_created(self, session_id: str, persona_id: str) -> None:
        """Phase 5 telemetry, fired once per brand-new session on a background thread
        (never blocks session start). cloud.emit_session_created is a hard no-op when
        signed out or opted out, and sends only content-free facts."""
        import threading

        import integrations.cloud as cloud
        from packages.config import load_config

        entry = self.personas.get(persona_id)
        family = entry.family if entry else ""
        workspace_kind = entry.workspace if entry else ""

        def _send() -> None:
            try:
                cloud.emit_session_created(
                    self.secrets,
                    load_config(),
                    session_id=session_id,
                    persona_id=persona_id,
                    persona_family=family,
                    workspace_kind=workspace_kind,
                )
            except Exception:
                pass  # telemetry must never surface as a session error

        threading.Thread(target=_send, daemon=True).start()


    # -- per-session live view --------------------------------------------------
    def register_event_client(self, send_cb: Any) -> None:
        self._event_clients.add(send_cb)


    def unregister_event_client(self, send_cb: Any) -> None:
        self._event_clients.discard(send_cb)


    def session_event(
        self, session_id: str, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        sequence = self._session_event_sequences.get(session_id, 0) + 1
        self._session_event_sequences[session_id] = sequence
        return runtime_event_v1(event_type, session_id, sequence, payload)


    async def broadcast_event(
        self, event_type: str, session_id: str | None, payload: dict[str, Any]
    ) -> None:
        """Fan an app-wide event out to every /ws/events socket. Best-effort: a dead
        socket is dropped, never fatal to the caller."""
        sequence = self._app_event_sequences.get(session_id, 0) + 1
        self._app_event_sequences[session_id] = sequence
        message = runtime_event_v1(event_type, session_id, sequence, payload)
        for cb in list(self._event_clients):
            try:
                await cb(message)
            except Exception:
                self.unregister_event_client(cb)


    def register_session_client(self, session_id: str, send_cb: Any) -> None:
        self._session_clients.setdefault(session_id, set()).add(send_cb)


    def unregister_session_client(self, session_id: str, send_cb: Any) -> None:
        clients = self._session_clients.get(session_id)
        if clients is not None:
            clients.discard(send_cb)
            if not clients:
                self._session_clients.pop(session_id, None)


    async def broadcast_session(
        self, session_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Fan a turn event out to every socket viewing this session. Best-effort: a dead socket
        is dropped, never fatal to the turn (delivery is socket-independent)."""
        message = self.session_event(session_id, event_type, payload)
        for cb in list(self._session_clients.get(session_id, ())):
            try:
                await cb(message)
            except Exception:
                self.unregister_session_client(session_id, cb)
