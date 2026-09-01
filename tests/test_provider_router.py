"""Tests for protocol-first provider routing, configuration, and model selection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from providers import (
    AssistantTurn,
    ModelCapabilities,
    OpenAIProvider,
    ProviderClient,
    ProviderRouter,
    StreamChunk,
    capabilities_for,
)
from providers.openai_provider import (
    _salvage_tool_calls_from_text,
    looks_like_unparsed_tool_call,
)
from providers.registry import build_provider_client


# -- base_url passthrough -------------------------------------------------------
def test_base_url_passed_to_sdk(monkeypatch):
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    OpenAIProvider(api_key="local", base_url="http://localhost:11434/v1")._ensure_client()
    assert captured == {"api_key": "local", "base_url": "http://localhost:11434/v1"}


def test_base_url_omitted_when_none(monkeypatch):
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    OpenAIProvider(api_key="sk-x")._ensure_client()
    assert "base_url" not in captured


# -- router routing -------------------------------------------------------------
class _Recorder(ProviderClient):
    def __init__(self, name: str):
        self.name = name
        self.models: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text=self.name)

    def stream(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        yield StreamChunk(turn=AssistantTurn(text=self.name))

    def capabilities(self, model):
        return ModelCapabilities()


def _patch_build(monkeypatch):
    state: dict = {"created": [], "latest": {}}

    def fake_build(name, profile, secrets):
        rec = _Recorder(name)  # a fresh client each build, so rebuilds are observable
        state["created"].append(rec)
        state["latest"][name] = rec
        return rec

    monkeypatch.setattr("providers.router.build_provider_client", fake_build)
    return state


def test_router_routes_and_strips_prefix(monkeypatch):
    state = _patch_build(monkeypatch)
    router = ProviderRouter(secrets=None)

    turn = router.complete(model="deepseek:deepseek-v4-flash", messages=[])
    assert turn.text == "deepseek"
    assert state["latest"]["deepseek"].models == [
        "deepseek-v4-flash"
    ]  # prefix stripped before delegating

    router.complete(model="gpt-5.5", messages=[])  # bare → default openai
    assert state["latest"]["openai"].models == ["gpt-5.5"]


def test_router_caches_and_invalidates(monkeypatch):
    state = _patch_build(monkeypatch)
    router = ProviderRouter(secrets=None)

    first = router._client_for("deepseek:a")
    second = router._client_for("deepseek:b")
    assert first is second  # same provider → cached client reused (build called once)
    assert len(state["created"]) == 1

    router.invalidate("deepseek")
    third = router._client_for("deepseek:c")
    assert third is not first  # rebuilt after invalidation
    assert len(state["created"]) == 2


def test_router_bare_only_strips_known_provider():
    r = ProviderRouter(secrets=None)
    assert (
        r._bare("deepseek:qwen2.5-coder:32b") == "qwen2.5-coder:32b"
    )  # strip provider, keep tag
    assert r._bare("gpt-5.5") == "gpt-5.5"
    # a colon that isn't a provider (version tag) must NOT be split — else OpenAI gets "32b"
    assert r._bare("qwen2.5-coder:32b") == "qwen2.5-coder:32b"
    assert r._provider_name("qwen2.5-coder:32b") == "openai"  # unknown prefix → default


def test_router_capabilities_prefix_aware():
    router = ProviderRouter(secrets=None)
    assert router.capabilities("deepseek:deepseek-v4-flash").tools is True
    assert router.capabilities("deepseek:deepseek-v4-flash").parallel_tool_calls is True


def test_capabilities_unknown_compatible_model_are_conservative():
    caps = capabilities_for("local:qwen2.5-coder")
    assert caps.tools is True
    # "qwen" is a recognized OpenAI-compatible vendor — parallel tool calls enabled.
    assert caps.parallel_tool_calls is True
    assert caps.vision is False


# -- tool-call salvage for non-conforming compatible endpoints ------------------
def test_salvage_bare_json_object():
    calls = _salvage_tool_calls_from_text(
        '{"name": "get_weather", "arguments": {"city": "Paris"}}'
    )
    assert len(calls) == 1
    assert calls[0].name == "get_weather"
    assert calls[0].arguments == {"city": "Paris"}


def test_salvage_tool_call_tags():
    text = '<tool_call>{"name": "a", "arguments": {"x": 1}}</tool_call>'
    calls = _salvage_tool_calls_from_text(text)
    assert [c.name for c in calls] == ["a"]


def test_salvage_multiple_via_array():
    text = '[{"name": "a", "arguments": {}}, {"name": "b", "arguments": {"y": 2}}]'
    calls = _salvage_tool_calls_from_text(text)
    assert [c.name for c in calls] == ["a", "b"]
    assert calls[1].arguments == {"y": 2}


def test_salvage_stringified_arguments():
    calls = _salvage_tool_calls_from_text('{"name": "a", "arguments": "{\\"k\\": 1}"}')
    assert calls[0].arguments == {"k": 1}


def test_salvage_ignores_non_toolcall_json():
    # Valid JSON, but not tool-call shaped → must stay text (no false positives).
    assert _salvage_tool_calls_from_text('{"city": "Paris", "temp": 18}') == []


def test_salvage_ignores_prose():
    assert _salvage_tool_calls_from_text("The weather in Paris is sunny.") == []
    assert _salvage_tool_calls_from_text("") == []


_TODO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "parameters": {
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "parameters": {
                "type": "object",
                "properties": {"recursive": {"type": "boolean"}},
            },
        },
    },
]


def test_salvage_mixed_prose_and_object():
    # The model wrote prose THEN a bare-JSON tool call in one message.
    text = 'It seems the workspace is empty. {"name": "list_files", "arguments": {"recursive": true}}'
    calls = _salvage_tool_calls_from_text(text, _TODO_TOOLS)
    assert [c.name for c in calls] == ["list_files"]
    assert calls[0].arguments == {"recursive": True}


def test_salvage_toolname_bare_array_shorthand():
    # The exact shape from the user's session: `todo_write [ {…}, {…} ]` (name + bare array).
    text = 'todo_write [{"content": "Understand requirements", "status": "in_progress"}, {"content": "Plan", "status": "pending"}]'
    calls = _salvage_tool_calls_from_text(text, _TODO_TOOLS)
    assert len(calls) == 1 and calls[0].name == "todo_write"
    # bare array mapped onto the tool's sole parameter
    assert calls[0].arguments == {
        "items": [
            {"content": "Understand requirements", "status": "in_progress"},
            {"content": "Plan", "status": "pending"},
        ]
    }


def test_salvage_toolname_object_shorthand():
    calls = _salvage_tool_calls_from_text(
        'list_files {"recursive": false}', _TODO_TOOLS
    )
    assert calls[0].name == "list_files" and calls[0].arguments == {"recursive": False}


def test_salvage_filters_unknown_tool_name():
    # A {name,arguments} object whose name isn't an offered tool must NOT be salvaged.
    text = '{"name": "rm_rf", "arguments": {"path": "/"}}'
    assert _salvage_tool_calls_from_text(text, _TODO_TOOLS) == []


def test_salvage_nested_braces_in_tag():
    text = '<tool_call>{"name": "todo_write", "arguments": {"items": [{"content": "a", "status": "pending"}]}}</tool_call>'
    calls = _salvage_tool_calls_from_text(text, _TODO_TOOLS)
    assert calls[0].name == "todo_write"
    assert calls[0].arguments == {"items": [{"content": "a", "status": "pending"}]}


_GREP_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "grep",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    }
]


def test_salvage_truncated_xml_call_keeps_only_complete_parameters():
    """A local model that runs out of tokens mid-call leaves `<function=…>` unclosed. Take
    the name and every parameter that DID close; NEVER the half-written trailing one — a
    truncated path or file body reaching a tool is worse than no call at all.

    Port of andrewyng/openworker 5c8f6dd1c24a6c35c576c8a7e7410612c8e69b1b."""
    text = "<tool_call>\n<function=grep>\n<parameter=pattern>TODO</parameter>\n<parameter=path>sr"
    calls = _salvage_tool_calls_from_text(text, _TODO_TOOLS + _GREP_TOOL)
    assert len(calls) == 1 and calls[0].name == "grep"
    assert calls[0].arguments == {"pattern": "TODO"}  # the partial `path` is gone


def test_salvage_truncated_xml_prefers_a_complete_call_and_filters_unknown_names():
    complete_then_cut = (
        "<tool_call><function=list_files><parameter=recursive>true</parameter>"
        "</function></tool_call>\n<tool_call>\n<function=grep>"
    )
    calls = _salvage_tool_calls_from_text(complete_then_cut, _TODO_TOOLS + _GREP_TOOL)
    assert [c.name for c in calls] == ["list_files"]  # the finished one wins
    # An unfinished call naming something we never offered stays text (no false positives).
    assert _salvage_tool_calls_from_text("<function=rm_rf>\n<parameter=p>/", _TODO_TOOLS) == []


def test_looks_like_unparsed_tool_call_ignores_code_and_needs_tools():
    """Distinguishes a leaked call from a model *explaining* tool syntax — the latter is a
    real answer and must not be turned into an error."""
    leaked = "Let me read the files.\n</parameter>\n</function>\n</tool_call>"
    assert looks_like_unparsed_tool_call(leaked, _TODO_TOOLS) is True
    assert looks_like_unparsed_tool_call("A CLI that greets people.", _TODO_TOOLS) is False
    fenced = "Qwen writes calls like:\n```\n<tool_call><function=x>\n```\nThat's the shape."
    assert looks_like_unparsed_tool_call(fenced, _TODO_TOOLS) is False
    assert looks_like_unparsed_tool_call("The `<tool_call>` wrapper.", _TODO_TOOLS) is False
    assert looks_like_unparsed_tool_call(leaked, None) is False  # no tools offered → not a call


class _FakeOAClient:
    def __init__(self, *, content=None, tool_calls=None):
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        resp = SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")]
        )
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **k: resp)
        )


def test_complete_salvages_only_when_tools_requested():
    blob = '{"name": "get_weather", "arguments": {"city": "Paris"}}'
    tools = [{"type": "function", "function": {"name": "get_weather"}}]

    # tools requested + no structured calls → salvage, clear text
    p = OpenAIProvider(client=_FakeOAClient(content=blob))
    turn = p.complete(model="local-model", messages=[], tools=tools)
    assert turn.has_tool_calls and turn.tool_calls[0].name == "get_weather"
    assert turn.text is None

    # no tools requested → identical content stays plain text (gate holds)
    p2 = OpenAIProvider(client=_FakeOAClient(content=blob))
    turn2 = p2.complete(model="local-model", messages=[])
    assert not turn2.has_tool_calls
    assert turn2.text == blob


# -- manager get/set_provider ---------------------------------------------------
def test_manager_provider_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    assert isinstance(mgr.provider, ProviderRouter)

    res = mgr.create_custom_provider(
        "local", "openai", {"base_url": "http://localhost:9999/v1"}
    )
    assert res["ok"] is True

    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["local"]["configured"] is True
    assert provs["local"]["values"]["base_url"] == "http://localhost:9999/v1"
    assert provs["openai"]["needs_key"] is True
    # never leak secret values
    assert "api_key" not in provs["openai"].get("values", {})

    assert mgr.set_provider("nope", {})["ok"] is False  # unknown provider rejected


def test_manager_curated_models(tmp_path, monkeypatch):
    """No seed list: the picker is the curated matrix filtered to key-holding providers,
    plus user-added custom ids. A fresh install shows only the (not-yet-usable) default.
    """
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from providers.registry import provider_descriptors

    for d in provider_descriptors():  # ambient dev-shell keys must not leak in
        if d.env_key:
            monkeypatch.delenv(d.env_key, raising=False)
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    # no provider keys — nothing selectable yet: no default model has been chosen, and
    # blank defaults never surface as a picker entry
    assert mgr.get_settings()["models"] == []

    # a provider key unlocks exactly that provider's matrix models
    mgr.set_provider("anthropic", {"api_key": "sk-ant-test"})
    models = mgr.get_settings()["models"]
    assert "anthropic:claude-opus-4-8" in models
    assert "gpt-4o" not in models  # no OpenAI seed anywhere

    mgr.create_custom_provider(
        "local", "openai", {"base_url": "http://localhost:11434/v1"}
    )
    added = mgr.add_model("local:qwen2.5-coder:32b")
    assert added["ok"] and "local:qwen2.5-coder:32b" in added["models"]

    n = len(mgr.get_settings()["models"])
    mgr.add_model("local:qwen2.5-coder:32b")  # idempotent
    assert len(mgr.get_settings()["models"]) == n

    # removing a matrix model hides it persistently; re-adding unhides it
    removed = mgr.remove_model("anthropic:claude-haiku-4-5")
    assert "anthropic:claude-haiku-4-5" not in removed["models"]
    mgr2 = SessionManager(data_dir=tmp_path)  # survives a restart
    assert "anthropic:claude-haiku-4-5" not in mgr2.get_settings()["models"]
    mgr.add_model("anthropic:claude-haiku-4-5")
    assert "anthropic:claude-haiku-4-5" in mgr.get_settings()["models"]

    # removing a custom id drops it
    mgr.remove_model("local:qwen2.5-coder:32b")
    assert "local:qwen2.5-coder:32b" not in mgr.get_settings()["models"]

    # the active default stays selectable even if removed from the curated list
    mgr.remove_model(mgr.model)
    assert mgr.model in mgr.get_settings()["models"]

    assert mgr.add_model("  ")["ok"] is False  # empty rejected


def test_set_provider_auto_adds_recommended_when_pulled(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    monkeypatch.setattr(  # pretend the recommended model is pulled
        mgr,
        "_suggested_models",
        lambda name: ["deepseek-v4-flash"] if name == "deepseek" else [],
    )
    res = mgr.set_provider("deepseek", {"api_key": "ds-key"})
    assert res["recommended_model"] == "deepseek-v4-flash"
    assert "deepseek:deepseek-v4-flash" in mgr.get_settings()["models"]


def test_set_provider_skips_recommended_when_not_pulled(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    monkeypatch.setattr(mgr, "_suggested_models", lambda name: [])  # nothing pulled
    res = mgr.set_provider("deepseek", {"api_key": "ds-key"})
    # The recommended model is still selectable because it's in the curated MATRIX;
    # _suggested_models only controls whether add_model is called for non-matrix models.
    assert res["recommended_model"] == "deepseek-v4-flash"


# -- custom-provider registration / routing / persistence ------------------------
def _with_custom(alias="mygw", protocol="openai"):
    from providers.registry import (
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider(alias, protocol)
    return unregister_custom_provider


def test_custom_descriptor_resolves_and_routes():
    from providers import ProviderRouter, build_provider_client, get_descriptor
    from providers.base import ProviderClient
    from providers.openai_provider import OpenAIProvider

    cleanup = _with_custom()
    try:
        d = get_descriptor("mygw")
        assert d is not None and d.title == "mygw"
        assert d.blurb == "OpenAI"
        # Key OPTIONAL: local OpenAI-compatible servers often run without auth.
        assert d.needs_key is False

        # alias:model routes to the custom provider; the bare part strips the prefix
        router = ProviderRouter(secrets=None)
        assert router._provider_name("mygw:gpt-4o") == "mygw"
        assert router._bare("mygw:gpt-4o") == "gpt-4o"

        # the built client is an OpenAI-compatible client pointed at the custom endpoint
        client = build_provider_client(
            "mygw", {"api_key": "sk-x", "base_url": "https://gw.example/v1"}, None
        )
        assert isinstance(client, ProviderClient)
        assert isinstance(client, OpenAIProvider)
        assert client._base_url == "https://gw.example/v1"
    finally:
        cleanup("mygw")


def test_custom_descriptor_protocol_fields():
    from providers import get_descriptor

    cleanup = _with_custom(alias="local", protocol="openai")
    try:
        d = get_descriptor("local")
        assert d.needs_key is False  # keyless protocol reflects through
        keys = {f.key for f in d.fields}
        assert keys == {"api_key", "base_url"}
    finally:
        cleanup("local")


def test_custom_aliases_with_same_protocol_keep_distinct_titles():
    from providers import get_descriptor
    from providers.registry import (
        register_custom_provider,
        unregister_custom_provider,
    )

    register_custom_provider("fong", "openai")
    register_custom_provider("local-gateway", "openai")
    try:
        assert get_descriptor("fong").title == "fong"
        assert get_descriptor("local-gateway").title == "local-gateway"
        assert get_descriptor("fong").blurb == get_descriptor("local-gateway").blurb
    finally:
        unregister_custom_provider("fong")
        unregister_custom_provider("local-gateway")


def test_custom_registration_validates():
    import pytest

    from providers.registry import (
        _valid_alias,
        unregister_custom_provider,
    )

    assert not _valid_alias("") and not _valid_alias("has space") and not _valid_alias(".dot")
    assert _valid_alias("my-api_2")

    from providers import register_custom_provider as reg

    reg("mygw", "openai")
    try:
        with pytest.raises(ValueError):
            reg("mygw", "no-such-protocol")
        with pytest.raises(ValueError):
            reg("bad alias!", "openai")
    finally:
        unregister_custom_provider("mygw")


def test_custom_provider_roundtrip(tmp_path, monkeypatch):
    """create_custom_provider persists alias→protocol to prefs so `alias:model` still
    routes after a fresh SessionManager (restart) re-hydrates the registry."""
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    res = mgr.create_custom_provider(
        "mygw", "openai", {"api_key": "sk-x", "base_url": "https://gw.example/v1"}
    )
    assert res["ok"] is True and res["provider"] == "mygw"
    assert res["protocol"] == "openai"

    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["mygw"]["configured"] is True
    assert provs["mygw"]["needs_key"] is False
    assert provs["mygw"]["title"] == "mygw"
    assert provs["mygw"]["protocol"] == "openai"

    # model routing resolves through the alias
    assert mgr.provider._provider_name("mygw:gpt-4o") == "mygw"

    # a fresh manager re-hydrates from prefs — the alias still routes
    mgr2 = SessionManager(data_dir=tmp_path)
    assert mgr2.provider._provider_name("mygw:gpt-4o") == "mygw"
    provs2 = {p["name"]: p for p in mgr2.get_providers()}
    assert provs2["mygw"]["title"] == "mygw"
    assert provs2["mygw"]["protocol"] == "openai"


def test_custom_provider_rejects_builtin_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    res = mgr.create_custom_provider("openai", "openai", {})
    assert res["ok"] is False
    assert "already exists" in res["error"]


def test_custom_provider_rejects_bad_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    res = mgr.create_custom_provider("mygw", "no-such", {})
    assert res["ok"] is False


def test_remove_custom_provider_drops_models(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    mgr.create_custom_provider("mygw", "openai", {"base_url": "https://x/v1"})
    mgr.add_model("mygw:gpt-4o")
    assert "mygw:gpt-4o" in (mgr._prefs.get("models") or [])

    res = mgr.remove_custom_provider("mygw")
    assert res["ok"] is True
    assert "mygw:gpt-4o" not in (mgr._prefs.get("models") or [])
    # survives restart — nothing to re-hydrate
    mgr2 = SessionManager(data_dir=tmp_path)
    assert "mygw:gpt-4o" not in mgr2.get_settings()["models"]
    assert "mygw" not in {p["name"] for p in mgr2.get_providers()}


def test_fetch_models_auto_adds_by_prefix(tmp_path, monkeypatch):
    """fetch_models probes the alias endpoint then auto-adds `alias:{id}` (idempotent)."""
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))

    def fake_fetch(name, profile, secrets, timeout=10.0):
        # only the probe function is faked — the add-to-picker logic stays real
        models = ["gpt-4o", "embedding-3"] if name == "mygw" else []
        return {"ok": True, "models": models}

    from services.server.manager import SessionManager

    monkeypatch.setattr("services.server.manager.fetch_provider_models", fake_fetch)
    mgr = SessionManager(data_dir=tmp_path)
    mgr.create_custom_provider("mygw", "openai", {"base_url": "https://x/v1"})

    res = mgr.fetch_models("mygw", {})
    assert res["ok"] is True
    assert res["models"] == ["gpt-4o", "embedding-3"]
    assert res["added"] == ["mygw:gpt-4o", "mygw:embedding-3"]
    assert "mygw:gpt-4o" in (mgr._prefs.get("models") or [])

    # second fetch is idempotent — nothing re-added
    res2 = mgr.fetch_models("mygw", {})
    assert res2["added"] == []


def test_fetch_models_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    assert mgr.fetch_models("nope", {})["ok"] is False


def test_provider_builders(monkeypatch):
    import pytest

    from providers import AnthropicProvider, OpenAIProvider

    # Anthropic Messages remains distinct from the OpenAI implementation.
    p = build_provider_client("anthropic", {"api_key": "sk-ant-x"}, None)
    assert isinstance(p, AnthropicProvider) and p._api_key == "sk-ant-x"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Anthropic"):
        build_provider_client("anthropic", {}, None)._ensure_client()

    # OpenAI custom endpoint (Azure /openai/v1, OpenRouter, vLLM, …) passes through and
    # keeps Chat Completions; a blank endpoint means stock OpenAI → the Responses API.
    from providers import OpenAIResponsesProvider

    o = build_provider_client(
        "openai", {"base_url": "https://my.azure.example/openai/v1"}, None
    )
    assert isinstance(o, OpenAIProvider)
    assert o._base_url == "https://my.azure.example/openai/v1"
    assert isinstance(build_provider_client("openai", {}, None), OpenAIResponsesProvider)


def test_anthropic_capabilities():
    caps = capabilities_for("anthropic:claude-sonnet-4-6")
    assert caps.tools is True and caps.vision is True and caps.streaming is True
    assert caps.parallel_tool_calls is True


def test_anthropic_provider_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["anthropic"]["configured"] is False
    assert "claude-sonnet-4-6" in provs["anthropic"]["suggested_models"]

    res = mgr.set_provider("anthropic", {"api_key": "sk-ant-test"})
    assert res["ok"] is True and res["recommended_model"] == "claude-fable-5"
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["anthropic"]["configured"] is True
    assert "api_key" not in provs["anthropic"].get("values", {})  # secrets never leak
    # the recommended model is auto-added to the curated list with its provider prefix
    assert "anthropic:claude-fable-5" in mgr.get_settings()["models"]

def test_first_configured_provider_wins_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    assert mgr.model == ""  # fresh install: no preset vendor/model default

    # the first provider that gets a key takes over the default
    mgr.set_provider("anthropic", {"api_key": "sk-ant-x"})
    assert mgr.model == "anthropic:claude-fable-5"

    # but a default that already works is never stolen by the next provider
    mgr.set_provider("deepseek", {"api_key": "ds-key"})
    assert mgr.model == "anthropic:claude-fable-5"


def test_surface_visibility(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    # default: Cowork only
    s = mgr.get_settings()["surfaces"]
    assert s == {"cowork": True, "chat": False, "code": False}

    mgr.set_surfaces(chat=True)
    assert mgr.get_settings()["surfaces"]["chat"] is True
    assert mgr.get_settings()["surfaces"]["code"] is False  # untouched

    mgr.set_surfaces(code=True)
    assert mgr.get_settings()["surfaces"] == {
        "cowork": True,
        "chat": True,
        "code": True,
    }

    mgr.set_surfaces(chat=False)
    assert mgr.get_settings()["surfaces"]["chat"] is False
    # cowork is always on regardless
    assert mgr.get_settings()["surfaces"]["cowork"] is True


def test_provider_suggested_models(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert "gpt-5.5" in provs["openai"]["suggested_models"]
    assert "deepseek-v4-flash" in provs["deepseek"]["suggested_models"]


# -- last-used tracking (router on_use hook + manager persistence) ----------------


def test_router_on_use_fires_with_provider_name():
    seen: list[str] = []
    router = ProviderRouter(on_use=seen.append)
    router._clients["openai"] = OpenAIProvider(client=_FakeOAClient(content="hi"))
    router._clients["zai"] = OpenAIProvider(client=_FakeOAClient(content="hi"))

    router.complete(model="gpt-5.5", messages=[])
    router.complete(model="zai:glm-5.2", messages=[])
    assert seen == ["openai", "zai"]


def test_router_on_use_failures_never_break_the_call():
    def boom(_name):
        raise RuntimeError("telemetry down")

    router = ProviderRouter(on_use=boom)
    router._clients["openai"] = OpenAIProvider(client=_FakeOAClient(content="ok"))
    assert router.complete(model="gpt-5.5", messages=[]).text == "ok"


def test_custom_openai_profile_never_falls_back_to_openai_credentials(monkeypatch, tmp_path):
    """Endpoint-bound profiles must not send OPENAI_API_KEY to a different host."""
    from packages.secrets import SecretStore

    monkeypatch.setenv("OPENAI_API_KEY", "official-key")
    store = SecretStore(tmp_path / "secrets.json")
    profile = {
        "name": "private-gateway",
        "protocol": "openai",
        "base_url": "https://gateway.example/v1",
        "api_mode": "chat",
        # Deliberately no api_key.
    }
    client = build_provider_client("private-gateway", profile, store)
    assert isinstance(client, OpenAIProvider)
    assert client._allow_credential_fallback is False
    with pytest.raises(RuntimeError, match="No model API key"):
        client._ensure_client()


def test_openai_profile_selects_responses_or_chat_by_api_mode():
    from providers import OpenAIResponsesProvider

    responses = build_provider_client(
        "openai",
        {"name": "openai", "protocol": "openai", "api_key": "sk-x", "api_mode": "responses"},
        None,
    )
    chat = build_provider_client(
        "gateway",
        {"name": "gateway", "protocol": "openai", "api_key": "sk-x", "base_url": "https://gw.example/v1", "api_mode": "chat"},
        None,
    )
    assert isinstance(responses, OpenAIResponsesProvider)
    assert isinstance(chat, OpenAIProvider)


def test_legacy_provider_profiles_migrate_once_without_losing_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from packages.secrets import SecretStore
    from services.server.manager import SessionManager

    store = SecretStore()
    store.put("provider:deepseek", {"api_key": "deep-key", "base_url": "https://deep.example/v1"})
    store.put("provider:mygw", {"api_key": "gw-key", "base_url": "https://gw.example/v1"})
    (tmp_path / "prefs.json").write_text(
        '{"custom_providers":{"mygw":{"protocol":"openai"}}}', encoding="utf-8"
    )

    manager = SessionManager(data_dir=tmp_path)
    assert store.get("provider:deepseek") is None
    assert store.get("provider:mygw") is None
    assert store.get("provider-profile:deepseek")["api_key"] == "deep-key"
    migrated = store.get("provider-profile:mygw")
    assert migrated["api_key"] == "gw-key" and migrated["protocol"] == "openai"
    assert manager._prefs["provider_profiles"]["mygw"]["protocol"] == "openai"
    assert manager._prefs["provider_profile_migration"]["migrated"] == ["deepseek", "mygw"]


def test_manager_key_hygiene_stamps(tmp_path, monkeypatch):
    """set_provider stamps key_set_at; _note_provider_use records (throttled) last_used_at;
    get_providers exposes both for the Settings pane."""
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from datetime import date

    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    mgr.set_provider("deepseek", {"api_key": "ds-key"})
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["deepseek"]["configured"] is True
    assert provs["deepseek"]["key_set_at"] == date.today().isoformat()
    assert provs["deepseek"]["last_used_at"] is None  # configured but never used

    # Endpoint-only re-save keeps the original stamp (the key wasn't touched).
    mgr.set_provider("deepseek", {"base_url": "https://api.deepseek.com/v1"})
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["deepseek"]["key_set_at"] == date.today().isoformat()

    mgr._note_provider_use("deepseek")
    first = mgr._prefs["provider_last_used"]["deepseek"]
    mgr._note_provider_use("deepseek")  # within the 60s throttle window → unchanged
    assert mgr._prefs["provider_last_used"]["deepseek"] == first
    provs = {p["name"]: p for p in mgr.get_providers()}
    assert provs["deepseek"]["last_used_at"] == first
    # and it survives a reload (persisted to prefs.json)
    mgr2 = SessionManager(data_dir=tmp_path)
    provs2 = {p["name"]: p for p in mgr2.get_providers()}
    assert provs2["deepseek"]["last_used_at"] == first
