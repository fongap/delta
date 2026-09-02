"""GitHubAppBroker — managed GitHub App installation token capability port.

A future Delta Hub implements this to mint installation access tokens for
GitHub App installations. The desktop caches tokens in memory (~50 min).

Current: NullGitHubAppBroker, which always returns "" (no token available).
GitHub PAT is NOT affected — it is fully local and needs no broker.
"""

from __future__ import annotations

from typing import Protocol


class GitHubAppBroker(Protocol):
    """Mint a GitHub App installation access token via a managed service.

    get_installation_token: Return a live installation access token string,
        or "" when unavailable (signed out / revoked / service unreachable).
    clear: Drop a cached token (disconnect / revocation).
    """

    def get_installation_token(
        self, installation_id: str, *, force: bool = False
    ) -> str:
        ...

    def clear(self, installation_id: str) -> None:
        ...


class NullGitHubAppBroker:
    """Default no-op GitHub App broker. Always returns empty string."""

    def get_installation_token(
        self, installation_id: str, *, force: bool = False
    ) -> str:
        return ""

    def clear(self, installation_id: str) -> None:
        pass
