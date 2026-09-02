"""GitHub installation metadata store.

`github:install:<installation_id>` holds ONE installation's metadata — account_login
(org/user the App is installed on), the connecting user's own github_login,
repo_selection, and that installation's inbound allow-list.

`github:default` is the manual connector profile (token=PAT). It does NOT carry
a relay flag — the managed relay path was removed in P1.
"""

from __future__ import annotations

from typing import Any

from packages.secrets import SecretStore

PREFIX = "github:install:"
DEFAULT_KEY = "github:default"


def _norm(value: Any) -> str:
    return str(value or "").strip()


def list_installs(secrets: SecretStore) -> list[tuple[str, dict[str, Any]]]:
    """(installation_id, profile) for every connected installation."""
    out = []
    for meta in secrets.status():
        key = meta.get("profile", "")
        if key.startswith(PREFIX):
            out.append((key[len(PREFIX) :], secrets.get(key) or {}))
    return sorted(out, key=lambda t: t[0])


def default_install(secrets: SecretStore) -> str:
    installs = dict(list_installs(secrets))
    pointer = _norm((secrets.get(DEFAULT_KEY) or {}).get("default_install"))
    if pointer in installs:
        return pointer
    return next(iter(installs), "")


def resolve(
    secrets: SecretStore, install: str = ""
) -> tuple[str, dict[str, Any] | None]:
    """(installation_id, profile) for the requested — or default — installation.
    Accepts the id or the account login (what agents see in results)."""
    installs = list_installs(secrets)
    wanted = _norm(install) or default_install(secrets)
    for installation_id, profile in installs:
        if wanted and (
            installation_id == wanted or _norm(profile.get("account_login")) == wanted
        ):
            return installation_id, profile
    return "", None


def connect_install(
    secrets: SecretStore, profile: dict[str, Any]
) -> dict[str, Any]:
    """Store a GitHub installation profile (metadata only — no token fields).

    Used for both manual PAT installs and installation metadata. Returns
    {ok, installation_id, account}. The caller is responsible for the token
    field if it's a manual PAT install."""
    installation_id = _norm(profile.get("installation_id"))
    if not installation_id:
        return {"ok": False, "error": "installation_id missing"}
    existing = secrets.get(PREFIX + installation_id) or {}
    merged = {
        "type": profile.get("type", "manual"),
        "managed": bool(profile.get("managed", False)),
        "installation_id": installation_id,
        "account_login": profile.get("account_login", ""),
        "account_type": profile.get("account_type", ""),
        "github_login": profile.get("github_login", ""),
        "repo_selection": profile.get("repo_selection", ""),
        "connection_id": profile.get("connection_id", ""),
    }
    if existing.get("allowed_users"):
        merged["allowed_users"] = list(existing["allowed_users"])
    elif profile.get("allowed_users"):
        merged["allowed_users"] = list(profile["allowed_users"])
    if existing.get("allow_all"):
        merged["allow_all"] = True
    elif profile.get("allow_all"):
        merged["allow_all"] = True
    secrets.put(PREFIX + installation_id, merged)
    default = secrets.get(DEFAULT_KEY) or {}
    default.setdefault("default_install", installation_id)
    secrets.put(DEFAULT_KEY, default)
    return {
        "ok": True,
        "account": merged["account_login"] or installation_id,
        "installation_id": installation_id,
    }


def disconnect_install(secrets: SecretStore, installation_id: str) -> dict[str, Any]:
    """Drop one installation. If no installations remain, clear default_install."""
    installation_id = _norm(installation_id)
    if not secrets.get(PREFIX + installation_id):
        return {"ok": False, "error": "installation not connected"}
    secrets.delete(PREFIX + installation_id)
    remaining = [i for i, _ in list_installs(secrets)]
    default = secrets.get(DEFAULT_KEY) or {}
    if _norm(default.get("default_install")) == installation_id:
        default.pop("default_install", None)
        if remaining:
            default["default_install"] = remaining[0]
    if not remaining:
        default.pop("default_install", None)
        if not any(default.get(k) for k in ("token", "access_token")):
            secrets.delete(DEFAULT_KEY)
            return {"ok": True, "remaining_installs": 0}
    secrets.put(DEFAULT_KEY, default)
    return {"ok": True, "remaining_installs": len(remaining)}
