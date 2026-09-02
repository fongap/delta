"""OAuthBroker — managed OAuth capability port.

A future Delta Hub implements this to broker OAuth flows (begin, exchange,
refresh, disconnect) for connectors that support managed one-click connect.
The desktop never sends scopes — the broker defines consent tiers.

Current: NullOAuthBroker, which always returns "unavailable".
Manual OAuth (browser flow + local token paste) is NOT affected by this
broker — it continues to work regardless.
"""

from __future__ import annotations

from typing import Any, Protocol


class OAuthBroker(Protocol):
    """Managed OAuth broker capability.

    begin:    Start a managed OAuth flow — returns the provider authorize URL
              for the browser to open.
    exchange: Complete the flow — receive the callback payload and return a
              connector profile dict (field-compatible with manual paste).
    refresh:  Renew a managed connector token before expiry.
    disconnect: Notify the broker that a managed connection is gone.
    """

    async def begin(
        self,
        connector: str,
        *,
        access: str = "",
        flow: str = "",
        redirect: str = "",
        app_state: str = "",
    ) -> dict[str, Any]:
        """Return {"authorize_url": ..., "app_state": ...} or {"ok": False, "error": ...}."""
        ...

    async def exchange(self, form: dict[str, str]) -> dict[str, Any]:
        """Receive the broker callback form-POST and return a connector profile."""
        ...

    async def refresh(
        self, connector: str, *, profile_key: str | None = None
    ) -> dict[str, Any] | None:
        """Renew a managed connector token; None if not applicable."""
        ...

    async def disconnect(
        self, connector: str, *, profile_key: str | None = None
    ) -> None:
        """Best-effort: tell the broker a connection is gone."""
        ...


class NullOAuthBroker:
    """Default no-op OAuth broker. All calls return "unavailable"."""

    async def begin(
        self,
        connector: str,
        *,
        access: str = "",
        flow: str = "",
        redirect: str = "",
        app_state: str = "",
    ) -> dict[str, Any]:
        from integrations.managed.errors import ManagedUnavailableError

        return {
            "ok": False,
            "error": ManagedUnavailableError.DEFAULT_MESSAGE,
            "signed_in": False,
        }

    async def exchange(self, form: dict[str, str]) -> dict[str, Any]:
        from integrations.managed.errors import ManagedUnavailableError

        return {"ok": False, "error": ManagedUnavailableError.DEFAULT_MESSAGE}

    async def refresh(
        self, connector: str, *, profile_key: str | None = None
    ) -> dict[str, Any] | None:
        return None

    async def disconnect(
        self, connector: str, *, profile_key: str | None = None
    ) -> None:
        pass
