"""GitHub REST and git CLI authentication helpers."""

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
    """Return PAT-backed headers or a connector error."""
    profile = secrets.get("github:default") or {}
    if profile.get("token"):
        return _github_headers(profile["token"]), None
    return {}, {"error": "github is not connected; missing token"}


def _github_git_auth_args(secrets: SecretStore, owner: str) -> list[str]:
    """Build per-invocation git auth without persisting the token."""
    import base64

    headers, err = _github_auth(secrets)
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
    """Run git without raising; return ``(stdout, error)``."""
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
    """Call the GitHub REST API with the configured PAT."""
    headers, err = _github_auth(secrets, install)
    if err:
        return err
    return _request(method, _github_base() + path, headers=headers, **kw)
