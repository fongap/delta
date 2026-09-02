"""Managed GitHub relay, desktop side (github-relay-spec 搂13 Step 3, MG3a).

Install callback 鈫?per-installation profiles (metadata only, NO tokens at
rest), the shared-hub adapter (fan-out by provider tag), the memory-only
installation-token client, and tool auth resolution (minted token for managed,
PAT untouched for manual). Hermetic: fake transports, stubbed broker calls.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from integrations.connectors import github_installs
from integrations.connectors.base import MessageEvent
from integrations.connectors.config import is_authorized, load_settings
from integrations.connectors.github_relay import GitHubRelayAdapter, split_thread
from integrations.connectors.relay_client import RelayHub, SlackRelayAdapter
from packages.secrets import SecretStore
from services.server import SessionManager, create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    with TestClient(app) as c:
        c.manager = manager
        yield c


def _install_form(installation_id: str, *, login="octocat", account="acme") -> dict:
    """The broker's loopback POST — deliberately NO token fields (§4)."""
    return {
        "connector": "github",
        "installation_id": installation_id,
        "account_login": account,
        "account_type": "Organization",
        "github_login": login,
        "repo_selection": "selected",
        "connection_id": f"conn_{installation_id}",
        "app_state": f"github-{installation_id}",
    }


# --- allow-list: per-installation scope (the per-workspace pattern) -----------


def test_github_settings_carry_per_installation_allowlists(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    secrets = SecretStore()
    github_installs.managed_connect_install(secrets, _install_form("101"))
    secrets.put(
        "github:install:101",
        {**secrets.get("github:install:101"), "allowed_users": ["octocat"]},
    )
    settings = load_settings(secrets)["github"]
    assert settings.enabled is True

    class Src:
        platform = "github"
        team_id = "101"
        user_id = "octocat"

    assert is_authorized(settings, Src()) is True
    Src.user_id = "stranger"
    assert is_authorized(settings, Src()) is False  # parks, not delivered
    Src.team_id = "999"
    assert is_authorized(settings, Src()) is False  # unknown installation


# --- adapter: shared hub fan-out, dispatch, lifecycle frames -------------------


class FakeTransport:
    def __init__(self, frames):
        self._q: asyncio.Queue = asyncio.Queue()
        for f in frames:
            self._q.put_nowait(f)

    async def open(self):
        pass

    async def recv(self):
        if not self._q.empty():
            return self._q.get_nowait()
        await asyncio.Event().wait()

    async def close(self):
        pass


def _gh_frame(**over):
    frame = {
        "provider": "github",
        "installation_id": "101",
        "owner_repo": "acme/site",
        "number": 7,
        "kind": "mention",
        "sender": "octocat",
        "title": "Broken build",
        "body": "@ocw please take a look",
        "url": "https://github.com/acme/site/issues/7",
        "address": "github:acme/site#7",
        "event_id": "d-1",
    }
    frame.update(over)
    return frame


def _slack_frame():
    return {
        "provider": "slack",
        "team_id": "T1",
        "channel": "C1",
        "event_id": "Ev1",
        "event": {"type": "app_mention", "user": "U_A", "channel": "C1", "text": "hi"},
    }


async def test_one_hub_fans_out_to_both_adapters(monkeypatch):
    """THE step-3 invariant: slack + github share one relay socket; frames land
    on their own adapter by provider tag."""
    monkeypatch.setenv("SLACK_API_URL", "http://127.0.0.1:9/")
    hub = RelayHub(
        "wss://relay.test/ws",
        lambda: "jwt",
        transport_factory=lambda: FakeTransport([_gh_frame(), _slack_frame()]),
    )
    slack = SlackRelayAdapter(
        "wss://relay.test/ws",
        lambda: "jwt",
        teams={"T1": {"bot_token": "xoxb-1", "bot_user_id": "UBOT"}},
        hub=hub,
    )
    github = GitHubRelayAdapter(hub, installs={"101": {"account_login": "acme"}})
    slack_events: list[MessageEvent] = []
    github_events: list[MessageEvent] = []

    async def on_slack(e):
        slack_events.append(e)

    async def on_github(e):
        github_events.append(e)

    slack.set_message_handler(on_slack)
    github.set_message_handler(on_github)
    assert await slack.connect() is True
    assert await github.connect() is True  # joins the running socket
    try:
        await hub.wait_dispatched(2, timeout=30.0)
    finally:
        await github.disconnect()
        await slack.disconnect()

    assert len(slack_events) == 1 and slack_events[0].source.platform == "slack"
    assert len(github_events) == 1
    gh = github_events[0]
    assert gh.source.platform == "github"
    assert gh.source.chat_id == "acme/site#7"
    assert gh.source.target == "github:acme/site#7"  # reply handle roundtrip
    assert gh.source.team_id == "101"  # allow-list scope = installation
    assert gh.source.user_id == "octocat"
    assert "Broken build" in gh.text and "@ocw please take a look" in gh.text


async def test_adapter_missed_and_revoked_frames():
    hub = RelayHub(
        "wss://x",
        lambda: "jwt",
        transport_factory=lambda: FakeTransport(
            [
                {
                    "provider": "github",
                    "kind": "missed",
                    "channel": "acme/site",
                    "count": 3,
                },
                {"provider": "github", "kind": "revoked", "installation_id": "101"},
            ]
        ),
    )
    adapter = GitHubRelayAdapter(hub, installs={"101": {"account_login": "acme"}})
    await adapter.connect()
    try:
        await hub.wait_dispatched(2, timeout=30.0)
    finally:
        await adapter.disconnect()
    assert adapter.missed == {"acme/site": 3}
    assert adapter.status()["installs"] == {}  # revoked 鈫?dropped


def test_addressing_roundtrip():
    assert split_thread("acme/site#7") == ("acme/site", 7)
    assert split_thread("acme/site") == ("acme/site", None)


# --- tools: minted token for managed, PAT untouched for manual -----------------


def _capture_requests(monkeypatch):
    # GitHub tools resolve `_request` in integration_github (extracted from integration_tools).
    from integrations.connectors import integration_github

    seen: list[dict] = []

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        seen.append({"method": method, "url": url, "headers": headers or {}})
        return {"ok": True, "data": {"items": []}}

    monkeypatch.setattr(integration_github, "_request", fake_request)
    return seen


def _tool(secrets, name):
    from integrations.connectors.integration_tools import make_integration_tools

    tools = make_integration_tools(secrets)
    return next(t for t in tools if t.__name__ == name)


def test_manual_pat_path_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    secrets = SecretStore()
    secrets.put("github:default", {"type": "token", "token": "ghp_manual"})
    seen = _capture_requests(monkeypatch)

    out = _tool(secrets, "github_get_issue")("acme", "site", 7)
    assert out["ok"] is True
    assert seen[0]["headers"]["Authorization"] == "Bearer ghp_manual"


def test_review_event_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    secrets = SecretStore()
    out = _tool(secrets, "github_review")("acme", "site", 5, "MERGE")
    assert "event must be" in out["error"]


# --- commits + clone/pull (activity summaries + local code exploration) --------


def test_list_commits_filters_and_trims(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from integrations.connectors import integration_github

    secrets = SecretStore()
    secrets.put("github:default", {"type": "token", "token": "ghp_x"})
    seen = {}

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        seen.update({"url": url, "params": params})
        return {
            "ok": True,
            "data": [
                {
                    "sha": "a" * 40,
                    "commit": {
                        "author": {"name": "Rohit", "date": "2026-07-08T10:00:00Z"},
                        "message": "Fix the flaky relay test\n\nlong body " * 40,
                    },
                    "author": {"login": "rohit-dev"},
                }
            ],
        }

    monkeypatch.setattr(integration_github, "_request", fake_request)
    out = _tool(secrets, "github_list_commits")(
        "acme",
        "site",
        since="2026-07-06T00:00:00Z",
        author="rohit-dev",
        max_results=200,
    )
    assert seen["url"].endswith("/repos/acme/site/commits")
    assert seen["params"]["since"] == "2026-07-06T00:00:00Z"
    assert seen["params"]["author"] == "rohit-dev"
    assert seen["params"]["per_page"] == 100  # capped
    (c,) = out["commits"]
    assert c["sha"] == "a" * 12 and c["author"] == "Rohit"
    assert len(c["message"]) <= 500  # trimmed for the model


def _git(args, cwd):
    import subprocess

    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def _origin(tmp_path):
    """A local 'GitHub': a bare repo at <base>/acme/site.git reachable via the
    GITHUB_GIT_URL override, plus a work repo to push new commits from."""
    base = tmp_path / "githost"
    bare = base / "acme" / "site.git"
    bare.mkdir(parents=True)
    _git(["init", "--bare", "--initial-branch=main", str(bare)], cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    _git(["init", "--initial-branch=main"], cwd=work)
    (work / "README.md").write_text("hello")
    _git(["add", "."], cwd=work)
    _git(["commit", "-m", "first"], cwd=work)
    _git(["remote", "add", "origin", str(bare)], cwd=work)
    _git(["push", "origin", "main"], cwd=work)
    return {"base": base, "work": work}


def _clone_tools(secrets, tmp_path):
    from integrations.connectors.integration_tools import make_integration_tools
    from core.roots import RootDir

    granted = tmp_path / "granted"
    granted.mkdir(exist_ok=True)
    tools = make_integration_tools(secrets, roots=[RootDir(granted, writable=True)])
    by_name = {t.__name__: t for t in tools}
    return granted, by_name


def test_clone_refuses_paths_outside_granted_roots(tmp_path, monkeypatch, _origin):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GITHUB_GIT_URL", f"file://{_origin['base']}")
    secrets = SecretStore()
    _granted, tools = _clone_tools(secrets, tmp_path)

    out = tools["github_clone"]("acme", "site", directory=str(tmp_path / "elsewhere"))
    assert "outside the session's writable directories" in out["error"]
    assert not (tmp_path / "elsewhere").exists()

    # and with no writable root at all 鈫?a clear error, no filesystem writes
    from integrations.connectors.integration_tools import make_integration_tools

    bare_tools = {t.__name__: t for t in make_integration_tools(secrets, roots=[])}
    out = bare_tools["github_clone"]("acme", "site")
    assert "no writable session directory" in out["error"]


def test_clone_refuses_non_empty_target(tmp_path, monkeypatch, _origin):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GITHUB_GIT_URL", f"file://{_origin['base']}")
    secrets = SecretStore()
    granted, tools = _clone_tools(secrets, tmp_path)
    (granted / "site").mkdir()
    (granted / "site" / "keep.txt").write_text("existing work")

    out = tools["github_clone"]("acme", "site")
    assert "not empty" in out["error"]
    assert (granted / "site" / "keep.txt").read_text() == "existing work"
