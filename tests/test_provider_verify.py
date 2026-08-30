"""Tests for provider key detection + the live (read-only) Test/verify path. SDK-free: the
single httpx.get is monkeypatched so no network is touched."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from providers import detect_provider, verify_provider_key


# -- detect_provider ------------------------------------------------------------
@pytest.mark.parametrize(
    "key,expected",
    [
        ("sk-ant-api03-abc", "anthropic"),
        ("sk-or-v1-abc", "openrouter"),
        ("AIzaSyAbc123", "gemini"),
        ("sk-proj-abc", "openai"),
        ("sk_live_abc", "openai"),
        ("", None),
        ("   ", None),
        ("nonsense", None),
    ],
)
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected


# -- verify_provider_key: status-code mapping + per-provider request shape -------
def _patch_get(monkeypatch, status=200, capture=None, raise_exc=None):
    def fake_get(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr("httpx.get", fake_get)


def test_verify_openai_ok(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": True}
    assert cap["url"] == "https://api.openai.com/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer sk-x"


def test_verify_openai_custom_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key(
        "openai", api_key="sk-x", base_url="https://gw.example/openai/v1/"
    )
    # trailing slash trimmed, /models appended to the custom endpoint
    assert cap["url"] == "https://gw.example/openai/v1/models"


def test_verify_bad_key_is_invalid(monkeypatch):
    _patch_get(monkeypatch, status=401)
    assert verify_provider_key("openai", api_key="sk-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


def test_verify_anthropic_headers(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("anthropic", api_key="sk-ant-x")
    assert cap["url"] == "https://api.anthropic.com/v1/models"
    assert cap["headers"]["x-api-key"] == "sk-ant-x"
    assert "anthropic-version" in cap["headers"]


def test_verify_gemini_key_param(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("gemini", api_key="AIza-x")
    assert cap["params"]["key"] == "AIza-x"


def test_verify_ollama_uses_v1_models_no_key(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("ollama", base_url="http://localhost:11434")
    assert cap["url"] == "http://localhost:11434/v1/models"
    assert "headers" not in cap  # keyless


def test_verify_network_error_is_clean(monkeypatch):
    _patch_get(monkeypatch, raise_exc=ConnectionError("boom"))
    res = verify_provider_key("openai", api_key="sk-x")
    assert res["ok"] is False
    assert "Couldn't reach" in res["error"]


def test_verify_unexpected_status(monkeypatch):
    _patch_get(monkeypatch, status=500)
    res = verify_provider_key("anthropic", api_key="sk-ant-x")
    assert res["ok"] is False
    assert "500" in res["error"]


# -- fetch_provider_models: same probe shape, but returns the parsed model id list --
def _patch_get_json(monkeypatch, status=200, payload=None, capture=None, raise_exc=None):
    def fake_get(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(
            status_code=status,
            json=lambda: payload or {"data": []},
        )

    monkeypatch.setattr("httpx.get", fake_get)


def test_fetch_openai_compatible_uses_alias_endpoint(monkeypatch):
    from providers import (
        fetch_provider_models,
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider("mygw", "openai-compatible")
    try:
        cap: dict = {}
        _patch_get_json(
            monkeypatch,
            payload={"data": [{"id": "gpt-4o"}, {"id": "claude-sonnet-4-6"}]},
            capture=cap,
        )
        res = fetch_provider_models(
            "mygw", {"api_key": "sk-x", "base_url": "https://gw.example/v1"}, None
        )
        assert res["ok"] is True
        assert res["models"] == ["gpt-4o", "claude-sonnet-4-6"]
        assert cap["url"] == "https://gw.example/v1/models"
        assert cap["headers"]["Authorization"] == "Bearer sk-x"
    finally:
        unregister_custom_provider("mygw")


def test_fetch_custom_anthropic_protocol_headers(monkeypatch):
    from providers import (
        fetch_provider_models,
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider("claude-gw", "anthropic")
    try:
        cap: dict = {}
        _patch_get_json(
            monkeypatch,
            payload={"data": [{"id": "claude-fable-5"}]},
            capture=cap,
        )
        res = fetch_provider_models("claude-gw", {"api_key": "sk-ant-x"}, None)
        assert res["ok"] is True and res["models"] == ["claude-fable-5"]
        assert cap["url"] == "https://api.anthropic.com/v1/models"
        assert cap["headers"]["x-api-key"] == "sk-ant-x"
        assert "anthropic-version" in cap["headers"]
    finally:
        unregister_custom_provider("claude-gw")


def test_fetch_custom_ollama_is_keyless(monkeypatch):
    from providers import (
        fetch_provider_models,
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider("local-llm", "ollama")
    try:
        cap: dict = {}
        _patch_get_json(
            monkeypatch,
            payload={"data": [{"id": "qwen3-coder:30b"}]},
            capture=cap,
        )
        res = fetch_provider_models("local-llm", {"base_url": "http://box:11434"}, None)
        assert res["ok"] is True and res["models"] == ["qwen3-coder:30b"]
        assert cap["url"] == "http://box:11434/v1/models"
        assert "headers" not in cap  # keyless
    finally:
        unregister_custom_provider("local-llm")


def test_fetch_bad_key_is_clean(monkeypatch):
    from providers import (
        fetch_provider_models,
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider("mygw", "openai-compatible")
    try:
        _patch_get_json(monkeypatch, status=401)
        res = fetch_provider_models(
            "mygw", {"api_key": "sk-bad", "base_url": "https://gw.example/v1"}, None
        )
        assert res == {"ok": False, "error": "Invalid API key."}
    finally:
        unregister_custom_provider("mygw")


def test_fetch_bedrock_is_unsupported(monkeypatch):
    from providers import (
        fetch_provider_models,
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider("aws-gw", "bedrock")
    try:
        res = fetch_provider_models("aws-gw", {"region": "us-east-1"}, None)
        assert res["ok"] is False
        assert "doesn't expose a model list" in res["error"]
    finally:
        unregister_custom_provider("aws-gw")
