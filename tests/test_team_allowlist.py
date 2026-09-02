"""Per-workspace allow-list and authorization for the Slack connector.

The managed relay path was removed in P1. Slack now has ONE mode: manual Socket
Mode (bot_token+app_token in `slack:default`). Per-workspace allow-lists are
still possible for users who run multiple manual installs (one per workspace)
keyed by `slack:team:<team_id>` profiles, but the relay-driven install flow
no longer exists.
"""

from __future__ import annotations

from integrations.connectors import (
    ConnectorSettings,
    Gateway,
    MessageEvent,
    SessionSource,
    TeamAuth,
    load_settings,
)
from integrations.connectors.config import is_authorized
from providers import ModelCapabilities, ProviderClient
from packages.secrets import SecretStore
from services.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _manual_manager(tmp_path) -> SessionManager:
    m = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    m.secrets.put(
        "slack:default",
        {
            "type": "manual",
            "bot_token": "xoxb-default",
            "app_token": "xapp-default",
            "enabled": True,
        },
    )
    return m


def _team_manager(tmp_path, *, teams=("T1",)) -> SessionManager:
    """A manager with both a default profile and per-workspace team profiles."""
    m = _manual_manager(tmp_path)
    for t in teams:
        m.secrets.put(
            f"slack:team:{t}",
            {
                "type": "oauth",
                "managed": True,
                "bot_token": f"xoxb-{t}",
                "team_id": t,
                "allowed_users": [],
            },
        )
    return m


# -- is_authorized ---------------------------------------------------------------
def test_is_authorized_team_scoped():
    s = ConnectorSettings(
        platform="slack",
        allowed_users={"U_FLAT"},
        teams={"T1": TeamAuth(allowed_users={"U_OK"}), "T2": TeamAuth()},
    )
    # authorized only via the event's OWN team's list
    assert is_authorized(s, SessionSource("slack", "C1", user_id="U_OK", team_id="T1"))
    assert not is_authorized(
        s, SessionSource("slack", "C1", user_id="U_OK", team_id="T2")
    )
    # the flat list never authorizes a team-scoped event
    assert not is_authorized(
        s, SessionSource("slack", "C1", user_id="U_FLAT", team_id="T1")
    )
    # unknown team = no install we know of → deny
    assert not is_authorized(
        s, SessionSource("slack", "C1", user_id="U_OK", team_id="T_UNKNOWN")
    )
    # per-team allow_all opens only that team
    s.teams["T2"].allow_all = True
    assert is_authorized(s, SessionSource("slack", "C1", user_id="U_X", team_id="T2"))
    assert not is_authorized(
        s, SessionSource("slack", "C1", user_id="U_X", team_id="T1")
    )


def test_is_authorized_flat_path_unchanged():
    # Manual Socket Mode sources carry no team_id → the flat list, exactly as before,
    # even when team lists exist alongside.
    s = ConnectorSettings(
        platform="slack",
        allowed_users={"U_FLAT"},
        teams={"T1": TeamAuth(allowed_users={"U_OK"}, allow_all=True)},
    )
    assert is_authorized(s, SessionSource("slack", "C1", user_id="U_FLAT"))
    assert not is_authorized(s, SessionSource("slack", "C1", user_id="U_OK"))
    s2 = ConnectorSettings(platform="slack", allow_all=True)
    assert is_authorized(s2, SessionSource("slack", "C1", user_id="anyone"))


# -- load_settings ---------------------------------------------------------------
def test_load_settings_populates_teams(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "slack:default",
        {"bot_token": "xoxb-default", "app_token": "xapp-default", "enabled": True},
    )
    secrets.put(
        "slack:team:T1",
        {"bot_token": "xoxb-1", "allowed_users": ["U_A", "U_B"]},
    )
    secrets.put("slack:team:T2", {"bot_token": "xoxb-2", "allow_all": True})
    settings = load_settings(secrets)
    slack = settings["slack"]
    assert slack.enabled is True
    assert slack.teams["T1"].allowed_users == {"U_A", "U_B"}
    assert slack.teams["T1"].allow_all is False
    assert slack.teams["T2"].allow_all is True


# -- manager write path ----------------------------------------------------------
def test_set_allowed_with_team_writes_team_profile(tmp_path):
    m = _team_manager(tmp_path, teams=("T1", "T2"))
    m.gateway = Gateway(
        secrets=m.secrets, settings={"slack": load_settings(m.secrets)["slack"]}
    )

    out = m.allow_user("slack", "U_NEW", team_id="T1")
    assert out["ok"] is True and out["team_id"] == "T1"
    assert m.secrets.get("slack:team:T1")["allowed_users"] == ["U_NEW"]
    # the sibling team and the flat list are untouched
    assert not m.secrets.get("slack:team:T2").get("allowed_users")
    assert not m.secrets.get("slack:default").get("allowed_users")
    # live gateway reflects it without a restart
    assert m.gateway.settings["slack"].teams["T1"].allowed_users == {"U_NEW"}

    m.disallow_user("slack", "U_NEW", team_id="T1")
    assert m.secrets.get("slack:team:T1")["allowed_users"] == []
    assert m.gateway.settings["slack"].teams["T1"].allowed_users == set()

    # unknown workspace → error, nothing written
    assert m.allow_user("slack", "U_X", team_id="T_NOPE")["ok"] is False


def test_set_allowed_without_team_keeps_flat_behavior(tmp_path):
    m = _team_manager(tmp_path)
    assert m.allow_user("slack", "U_FLAT")["allowed_users"] == ["U_FLAT"]
    assert m.secrets.get("slack:default")["allowed_users"] == ["U_FLAT"]
    assert not m.secrets.get("slack:team:T1").get("allowed_users")


# -- park + resolve --------------------------------------------------------------
async def test_park_carries_team_and_resolve_allows_into_team(tmp_path):
    m = _team_manager(tmp_path, teams=("T1",))
    delivered: list[MessageEvent] = []

    async def _capture(event: MessageEvent) -> None:
        delivered.append(event)

    m._dispatch_inbound = _capture

    event = MessageEvent(
        text="hello from T1",
        source=SessionSource(
            "slack", "C9", user_id="U_STRANGER", user_name="Zed", team_id="T1"
        ),
    )
    await m._park_unauthorized(event)
    items = m.parked.list("slack")
    assert items[0]["team_id"] == "T1"

    out = await m.resolve_unauthorized("slack", items[0]["id"], "allow_deliver")
    assert out["ok"] is True
    # the allow landed on the WORKSPACE list, not the flat one
    assert m.secrets.get("slack:team:T1")["allowed_users"] == ["U_STRANGER"]
    assert not m.secrets.get("slack:default").get("allowed_users")
    # the replayed event keeps its workspace, so per-team auth re-checks correctly
    assert len(delivered) == 1
    assert delivered[0].source.team_id == "T1"
    assert delivered[0].text == "hello from T1"


async def test_resolve_teamless_parked_uses_flat_list(tmp_path):
    # Manual-mode parked items (no team_id) keep resolving into slack:default.
    m = _team_manager(tmp_path)

    async def _noop(event) -> None:
        pass

    m._dispatch_inbound = _noop
    await m._park_unauthorized(
        MessageEvent(
            text="hi",
            source=SessionSource("slack", "D1", user_id="U_M", chat_type="dm"),
        )
    )
    item = m.parked.list("slack")[0]
    assert item["team_id"] is None
    assert (await m.resolve_unauthorized("slack", item["id"], "allow"))["ok"] is True
    assert m.secrets.get("slack:default")["allowed_users"] == ["U_M"]
    assert not m.secrets.get("slack:team:T1").get("allowed_users")
