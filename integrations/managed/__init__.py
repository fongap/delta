"""Managed Capability Ports — Delta Hub architecture baseline.

Delta Desktop can operate fully without any managed service (local-first).
These protocols define the capability boundaries a future Delta Hub (or an
optional OpenWorker Federation Adapter) can implement to unlock managed OAuth,
relay, GitHub App token mint, and external identity federation.

Current status: only interfaces and Null* defaults exist. No real broker is
wired. Removing this entire package leaves Desktop fully functional via
manual/local connector paths.

See ADR-004 for the full decision record.
"""

from __future__ import annotations

from integrations.managed.errors import ManagedUnavailableError
from integrations.managed.github_app import GitHubAppBroker, NullGitHubAppBroker
from integrations.managed.identity import (
    ExternalIdentity,
    ExternalIdentityProvider,
    NullIdentityProvider,
)
from integrations.managed.models import ManagedConfig
from integrations.managed.oauth import NullOAuthBroker, OAuthBroker
from integrations.managed.relay import NullRelayTransport, RelayTransport

__all__ = [
    "ExternalIdentity",
    "ExternalIdentityProvider",
    "NullIdentityProvider",
    "GitHubAppBroker",
    "NullGitHubAppBroker",
    "ManagedConfig",
    "ManagedUnavailableError",
    "NullOAuthBroker",
    "NullRelayTransport",
    "OAuthBroker",
    "RelayTransport",
]
