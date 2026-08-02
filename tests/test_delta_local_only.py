"""Delta's local-only boundary blocks legacy OpenWorker Cloud paths."""

from __future__ import annotations

from coworker import cloud
from coworker.config import Config
from coworker.secrets import SecretStore


def test_delta_config_does_not_ship_cloud_endpoints():
    config = Config()

    assert config.cloud_base_url == ""
    assert config.cloud_auth_domain == ""
    assert config.cloud_relay_ws_url == ""


def test_cloud_login_is_disabled_without_a_network_request(monkeypatch):
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Delta must not call OpenWorker Cloud")

    monkeypatch.setattr(cloud.httpx, "post", unexpected_request)

    result = cloud.begin_login(Config())

    assert result["ok"] is False
    assert result["local_only"] is True


def test_legacy_cloud_profiles_cannot_emit_network_traffic(tmp_path, monkeypatch):
    secrets = SecretStore(path=tmp_path / "secrets.json")
    secrets.put(cloud.CLOUD_AUTH_PROFILE, {"access_token": "legacy-token"})
    monkeypatch.setattr(
        cloud.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Delta must not call OpenWorker Cloud")
        ),
    )

    assert cloud.fresh_access_token(secrets, Config()) is None
    assert cloud.gallery_list(secrets, Config()) is None
    assert cloud.status(secrets)["signed_in"] is False
