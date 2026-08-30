"""Endpoint capability profile (v0.3.0 P0): per-endpoint Chat Completions params —
declared in the profile, learned from rejections, merged explicitly-wins."""

from __future__ import annotations

import types

import pytest

from providers.endpoint import (
    EndpointCaps,
    from_profile,
    learned_caps,
    merge,
    record_rejection,
)
from providers.openai_provider import OpenAIProvider


def test_from_profile_parses_declared_fields():
    caps = from_profile(
        {
            "base_url": "http://gw/v1",
            "stream_options": "false",
            "reasoning_content": False,
            "max_context": "32000",
        }
    )
    assert caps.stream_options is False
    assert caps.reasoning_content is False
    assert caps.parallel_tool_calls is True
    assert caps.max_context == 32000


def test_from_profile_ignores_garbage():
    caps = from_profile({"stream_options": "sometimes", "max_context": "soon"})
    assert caps == EndpointCaps()


class _FakeCompletions:
    """Raises a stream_options rejection once, then records the winning kwargs."""

    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1 and "stream_options" in kwargs:
            raise RuntimeError("'stream_options' is not supported by this server")
        # One compliant final chunk: an empty choices list + finish_reason stop.
        return [
            types.SimpleNamespace(
                usage=None,
                choices=[
                    types.SimpleNamespace(delta=None, finish_reason="stop"),
                ],
            )
        ]

    @property
    def chat(self):
        return types.SimpleNamespace(completions=self)


def test_unsupported_stream_options_is_learned_and_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    key = "http://gw:8000/v1"
    provider = OpenAIProvider(
        client=_FakeCompletions(), api_key="k", base_url=key, endpoint_key=key
    )

    # First call: sends stream_options, eats the 400, retries without it…
    first = list(provider.stream(model="m", messages=[{"role": "user", "content": "x"}]))
    assert len(first) == 1  # still a (chunk-less) successful stream
    fake = provider._client
    assert "stream_options" in fake.calls[0]
    assert "stream_options" not in fake.calls[1]

    # …and the rejection is remembered for this endpoint.
    assert learned_caps(key)["stream_options"] is False

    # Next call: the param is never sent — no failed round trip.
    list(provider.stream(model="m", messages=[{"role": "user", "content": "x"}]))
    assert "stream_options" not in fake.calls[2]


def test_explicit_profile_beats_learned(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    key = "http://gw:8000/v1"
    record_rejection(key, "stream_options")
    # The user's explicit profile word is final — a declared stream_options=true beats
    # a learned false (the reactive retry stays honest if the user is wrong). An
    # UNDECLARED default must not re-enable the rejected param, though.
    assert merge(learned_caps(key), from_profile({"stream_options": True})).stream_options
    assert not merge(learned_caps(key), None).stream_options
    assert not merge(learned_caps(key), from_profile({})).stream_options


def test_unknown_endpoint_uses_defaults():
    assert merge({}, None) == EndpointCaps()
    assert merge({}, None).max_context is None


def test_profile_flows_through_registry_builders():
    from providers.registry import _build_openai_compat

    provider = _build_openai_compat(
        {"base_url": "http://gw:9000/v1", "api_key": "k", "stream_options": False},
        secrets=None,
    )
    assert isinstance(provider, OpenAIProvider)
    assert provider._effective_caps().stream_options is False
    assert provider._endpoint_key == "http://gw:9000/v1"
