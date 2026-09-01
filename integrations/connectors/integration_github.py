"""GitHub connector helpers (REST API + git clone/pull auth).

Split out of ``integration_tools.py``: the GitHub tool closures there call these through module
globals, so re-importing by the same names keeps behavior identical. This family covers both the
REST API path (PAT or managed relay installation token) and the git CLI auth path (token rides an
HTTP header only, never persisted — the no-token-at-rest rule, github-relay-spec §4).
"""

from __future__ import annotations

from typing import Any

from packages.secrets import SecretStore
from integrations.connectors.integration_helpers import _request


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_base() -> str:
    import os

    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _github_auth(
    secrets: SecretStore, install: str = "", *, force: bool = False
) -> tuple[dict[str, str], dict[str, str] | None]:
    """(headers, err). A manual PAT (`github:default.token`) wins, untouched;
    a managed relay profile mints a short-lived installation token instead —
    memory-cached, never stored (github-relay-spec §4). `install` picks the
    installation by account login (pass the repo owner) or id; unknown values
    fall back to the default installation."""
    profile = secrets.get("github:default") or {}
    if profile.get("token"):
        return _github_headers(profile["token"]), None
    if profile.get("mode") == "relay":
        from integrations.connectors import github_installs

        installation_id, _prof = github_installs.resolve(secrets, install)
        if not installation_id and install:
            installation_id, _prof = github_installs.resolve(secrets, "")
        if not installation_id:
            return {}, {"error": "github is not connected; no App installation"}
        return {}, {
            "error": "github managed relay is unavailable "
            "(no managed service configured; use a PAT instead)"
        }
    return {}, {"error": "github is not connected; missing token"}


def _github_git_auth_args(secrets: SecretStore, owner: str) -> list[str]:
    """Per-invocation git auth: the token rides an HTTP header on the command
    line only — it must NEVER land in .git/config or a credential store (the
    no-token-at-rest rule; github-relay-spec §4). Empty for the tokenless case
    (public repos clone fine without auth)."""
    import base64

    headers, err = _github_auth(secrets, owner)
    if err:
        return ["-c", "credential.helper="]
    token = headers["Authorization"].split(" ", 1)[1]
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        "-c",
        f"http.extraHeader=AUTHORIZATION: basic {basic}",
        "-c",
        "credential.helper=",
    ]


def _run_git(
    args: list[str], *, cwd: Any = None, timeout: int = 600
) -> tuple[str, str]:
    """(stdout, error). Never raises; the error string is capped and carries no
    auth material (git never echoes header values)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return "", "git is not installed"
    except subprocess.TimeoutExpired:
        return "", "git timed out"
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout).strip()[-500:]
    return proc.stdout.strip(), ""


def _github_git_base() -> str:
    import os

    return os.environ.get("GITHUB_GIT_URL", "https://github.com").rstrip("/")


def _github_call(
    secrets: SecretStore, method: str, path: str, *, install: str = "", **kw: Any
) -> dict[str, Any]:
    """A GitHub API call that works on either auth path. A 401 on the managed
    path re-mints once (the cached installation token may have just expired)."""
    headers, err = _github_auth(secrets, install)
    if err:
        return err
    out = _request(method, _github_base() + path, headers=headers, **kw)
    managed = not (secrets.get("github:default") or {}).get("token")
    if managed and out.get("error") == "HTTP 401":
        headers, err = _github_auth(secrets, install, force=True)
        if err:
            return out
        out = _request(method, _github_base() + path, headers=headers, **kw)
    return out
