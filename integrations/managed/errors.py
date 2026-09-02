"""Managed Capability errors.

When no managed service is configured, broker calls raise/return these so
callers can show the user a clear "managed unavailable" message instead of
silently falling back to an OpenWorker endpoint.
"""

from __future__ import annotations


class ManagedUnavailableError(RuntimeError):
    """Raised when a managed capability is invoked but no managed service is
    configured. The message is user-facing and should be surfaced verbatim."""

    DEFAULT_MESSAGE = (
        "Managed capability is unavailable because no managed service is configured."
    )

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.DEFAULT_MESSAGE)
