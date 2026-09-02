"""RelayTransport — managed relay capability port.

The frame contract is decoded JSON dicts. Implementations lazy-import their
WebSocket library.

This module defines only the transport interface + a Null default. The relay
code path was removed from the desktop in P1; if/when a managed relay adapter
is implemented, it will live in `integrations/managed/adapters/<provider>.py`
and provide a `RelayTransport` implementation.

When no managed relay is configured, NullRelayTransport is used and the relay
stays disconnected — manual Socket Mode / PAT paths are unaffected.
"""

from __future__ import annotations

from typing import Protocol


class RelayTransport(Protocol):
    """One live connection to a managed relay. Implementations lazy-import
    their WebSocket library; the frame contract is decoded JSON dicts."""

    async def open(self) -> None: ...

    async def recv(self) -> dict | None:
        """Next frame, or None when the connection has closed."""
        ...

    async def close(self) -> None: ...


class NullRelayTransport:
    """Default no-op relay transport. recv() always returns None (closed)."""

    async def open(self) -> None:
        pass

    async def recv(self) -> dict | None:
        return None

    async def close(self) -> None:
        pass
