"""External identity — platform-agnostic federation model.

For future Delta Hub identity federation. Core types must NOT contain
OpenWorker-specific fields (no openworker_user_id, no openworker_email,
no openworker_jwt). An ExternalIdentity is just an issuer + subject +
optional display name — the same shape any OIDC / SAML / custom federation
adapter would produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExternalIdentity:
    """A verified external identity assertion.

    issuer:   URL or stable identifier of the identity provider.
    subject:  Stable per-issuer subject identifier (e.g. sub claim).
    display_name: Optional human-readable label (email, name, etc.).
    """

    issuer: str
    subject: str
    display_name: str | None = None


class ExternalIdentityProvider(Protocol):
    """Verify an external identity assertion (future federation).

    A future OpenWorker Federation Adapter would implement this to verify
    OpenWorker-issued tokens. An OIDC provider would implement this to
    verify ID tokens. Delta Hub's native device-token auth does NOT use
    this — device tokens are verified directly by the Hub.
    """

    async def verify_assertion(self, assertion: str) -> ExternalIdentity:
        """Verify a bearer assertion and return the external identity.

        Raises ManagedUnavailableError when no identity provider is configured.
        """
        ...


class NullIdentityProvider:
    """Default no-op identity provider. Always raises."""

    async def verify_assertion(self, assertion: str) -> ExternalIdentity:
        from integrations.managed.errors import ManagedUnavailableError

        raise ManagedUnavailableError(
            "No external identity provider is configured."
        )
