"""Config loading — layered defaults < global < workspace."""

from __future__ import annotations

from delta.config import load_config


def test_defaults_when_no_files(tmp_path):
    cfg = load_config(global_path=tmp_path / "nope.toml")
    assert cfg.model == ""
    assert cfg.mode == "interactive"
    assert cfg.max_iterations == 150
    assert cfg.allowed_commands == []


def test_global_and_workspace_override(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text('model = "gpt-4o"\nmax_iterations = 20\nport = 9000\n')
    ws = tmp_path / "ws"
    (ws / ".delta").mkdir(parents=True)
    (ws / ".delta" / "config.toml").write_text(
        'max_iterations = 30\nmode = "plan"\n'
    )

    cfg = load_config(ws, global_path=g)
    assert cfg.model == "gpt-4o"  # from global
    assert cfg.port == 9000  # from global
    assert cfg.max_iterations == 30  # workspace overrides global
    assert cfg.mode == "plan"  # from workspace


def test_workspace_cannot_grant_its_own_permissions(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text(
        'allowed_commands = ["git status"]\nauto_allow = ["write_file"]\n'
    )
    ws = tmp_path / "ws"
    (ws / ".delta").mkdir(parents=True)
    (ws / ".delta" / "config.toml").write_text(
        'allowed_commands = ["python3"]\nauto_allow = ["run_shell"]\n'
    )

    cfg = load_config(ws, global_path=g)
    assert cfg.allowed_commands == ["git status"]
    assert cfg.auto_allow == ["write_file"]


def test_trusted_workspace_adds_its_command_allowances_only(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text(
        'allowed_commands = ["git status"]\nauto_allow = ["write_file"]\n'
    )
    ws = tmp_path / "ws"
    (ws / ".delta").mkdir(parents=True)
    (ws / ".delta" / "config.toml").write_text(
        'allowed_commands = ["pytest", "git status"]\n'
        'auto_allow = ["run_shell"]\n'
    )

    cfg = load_config(ws, global_path=g, workspace_trusted=True)
    assert cfg.allowed_commands == ["git status", "pytest"]
    assert cfg.auto_allow == ["write_file"]


def test_workspace_trust_is_canonical_and_user_owned(tmp_path):
    import pytest

    from delta.workspace_trust import WorkspaceTrustStore

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink privilege required (canonicalization untestable): {exc}")
    store = WorkspaceTrustStore(tmp_path / "state" / "workspace_trust.json")

    canonical = store.set_trusted(alias, True)
    assert canonical == str(real.resolve())
    assert store.is_trusted(real)
    assert store.list() == [str(real.resolve())]
    import os

    if os.name == "posix":
        # os.chmod(0o600) is a no-op on Windows (the icacls ACL is applied instead).
        assert (store.path.stat().st_mode & 0o777) == 0o600

    store.set_trusted(real, False)
    assert not store.is_trusted(alias)

    store.path.write_text("[]")
    assert store.list() == []


    store.path.write_text("[]")
    assert store.list() == []


def test_workspace_trust_marks_acl_degradation(tmp_path, monkeypatch):
    """The trust file shares the SecretStore's best-effort private write; a failed
    hardening must leave a visible marker (cleared by a later verified write) —
    never degrade silently."""
    from delta import workspace_trust as wt

    store = wt.WorkspaceTrustStore(tmp_path / "state" / "workspace_trust.json")
    real = tmp_path / "ws"
    real.mkdir()

    monkeypatch.setattr(wt, "verify_user_restricted", lambda p: False)
    store.set_trusted(real, True)
    assert store.acl_unprotected()
    assert store._acl_marker_path().is_file()

    monkeypatch.setattr(wt, "verify_user_restricted", lambda p: True)
    store.set_trusted(real, True)  # a later verified write clears the marker
    assert not store.acl_unprotected()


def test_build_engine_honors_explicit_empty_command_allowlist(tmp_path):
    from delta.agent import build_code_engine
    from delta.config import global_config_path

    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text('allowed_commands = ["pytest"]\n')

    class _Stub:
        def complete(self, **k):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, m):  # pragma: no cover
            raise NotImplementedError

    engine = build_code_engine(
        workspace=tmp_path, provider=_Stub(), allowed_commands=[]
    )
    try:
        decision = engine.permissions.evaluate(
            "run_shell", {"command": "pytest -q"}, None
        )
        assert not decision.allowed and decision.needs_user
    finally:
        engine.executor.close()


def test_build_engine_respects_max_iterations(tmp_path):
    (tmp_path / ".delta").mkdir()
    (tmp_path / ".delta" / "config.toml").write_text("max_iterations = 3\n")

    from delta.agent import build_code_engine

    class _Stub:
        def complete(self, **k):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, m):  # pragma: no cover
            raise NotImplementedError

    engine = build_code_engine(workspace=tmp_path, provider=_Stub())
    try:
        assert engine.max_iterations == 3
    finally:
        engine.executor.close()


def test_cloud_endpoints_default_to_production():
    """A fresh install is local-first: no cloud traffic and no relaying until the user
    opts in. The base URL is the default sign-in endpoint (an address — nothing is sent
    until sign-in); the managed relay is DEFAULT-EMPTY so inbound relaying is OFF out of
    the box (empty ⇒ relay disabled; the user enables it explicitly or via sign-in)."""
    from delta.config import Config

    cfg = Config()
    assert cfg.cloud_base_url == "https://api.openworker.com"
    assert cfg.cloud_relay_ws_url == ""
