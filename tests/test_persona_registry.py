"""Phase 1 gate — persona registry lifecycle (delta-only, default + fallback).

R6.0 converged the registry to a single registered persona (Delta). Unknown, missing, or
legacy ids (code/chat/ops/myhelper) all resolve to Delta so historical sessions keep
working."""

from __future__ import annotations

import pytest

from core.personas.registry import DEFAULT_PERSONA_ID, PersonaRegistry


def _reg(tmp_path) -> PersonaRegistry:
    return PersonaRegistry(state_path=tmp_path / "personas.json")


def test_delta_is_the_only_builtin(tmp_path):
    reg = _reg(tmp_path)
    assert set(reg.ids()) == {"delta"}
    assert reg.get("delta").builtin is True


def test_sidebar_is_delta_only(tmp_path):
    reg = _reg(tmp_path)
    sidebar = reg.sidebar()
    assert [e["name"] for e in sidebar] == ["delta"]
    assert sidebar[0]["default"] is True


def test_surface_toggle_keeps_delta_resolvable(tmp_path):
    reg = _reg(tmp_path)
    reg.set_surfaced("delta", False)
    assert reg.sidebar() == []
    # Still installed + still resolvable.
    assert "delta" in reg.ids()
    assert reg.agent("delta").name == "delta"


def test_default_is_delta(tmp_path):
    reg = _reg(tmp_path)
    assert reg.default_id() == DEFAULT_PERSONA_ID == "delta"


def test_set_default_and_persists(tmp_path):
    reg = _reg(tmp_path)
    reg.set_default("delta")
    assert reg.default_id() == "delta" and reg.is_enabled("delta")
    reg2 = _reg(tmp_path)
    assert reg2.default_id() == "delta"


def test_agent_resolution_and_legacy_fallback(tmp_path):
    reg = _reg(tmp_path)
    assert reg.agent("delta").name == "delta"
    assert reg.agent("delta").family == "knowledge"
    # Historical ids resolve to the default (Delta) — never 404, never data loss.
    assert reg.agent("code").name == "delta"
    assert reg.agent("chat").name == "delta"
    assert reg.agent("ops").name == "delta"
    assert reg.agent("myhelper").name == "delta"
    assert reg.agent("does-not-exist").name == "delta"
    assert reg.agent(None).name == "delta"


def test_delta_keeps_workspace_enum(tmp_path):
    reg = _reg(tmp_path)
    ws = {p["id"]: p["workspace"] for p in reg.list_all()}
    assert ws == {"delta": "deliverable"}


def test_set_unknown_persona_raises(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(KeyError):
        reg.set_enabled("ghost", False)
    with pytest.raises(KeyError):
        reg.set_default("ghost")


def test_uninstall_builtin_raises(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(ValueError):
        reg.uninstall("delta")