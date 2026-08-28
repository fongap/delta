"""Auth/header builders for the non-GitHub connector families.

Split out of ``integration_tools.py``: these small helpers build request headers, base URLs,
and the Linear GraphQL request for the Google/Microsoft/Atlassian/GitLab/QuickBooks connector
tools. They are pure (or call ``_request`` directly), with no state, so the tool factory
re-imports them by the same names and behavior is unchanged.
"""

from __future__ import annotations

from typing import Any

from .integration_helpers import _request


def _google_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _graph_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _basic_auth(email: str, token: str) -> tuple[str, str]:
    return (email, token)


def _atlassian_base(profile: dict[str, Any]) -> str:
    return str(profile.get("base_url", "")).rstrip("/")


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _gitlab_api(profile: dict[str, Any]) -> str:
    base = str(profile.get("base_url") or "https://gitlab.com").rstrip("/")
    return f"{base}/api/v4"


def _qbo_base(profile: dict[str, Any]) -> str:
    env = str(profile.get("environment", "")).lower()
    host = (
        "sandbox-quickbooks.api.intuit.com"
        if env.startswith("sand")
        else "quickbooks.api.intuit.com"
    )
    return f"https://{host}/v3/company/{profile['realm_id']}"
