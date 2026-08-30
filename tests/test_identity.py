"""Product-identity tests — local interception, answer format, model-name source.

Identity questions must be answered locally (no model call) with the live session
model's display name; normal questions must fall through to the model.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from core.engine import TurnEngine
from core.events import EventType
from core.identity import (
    DEVELOPER,
    PRODUCT_NAME,
    answer,
    display_model_name,
    match_identity,
)
from core.permissions import PermissionEngine
from providers import AssistantTurn, ModelCapabilities, ProviderClient
from integrations.tools import ToolRegistry


class _ScriptedProvider(ProviderClient):
    def __init__(self, turn):
        self._turn = turn
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self._turn

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, model):
    provider = _ScriptedProvider(AssistantTurn(text="model reply", finish_reason="stop"))
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path)
    return (
        TurnEngine(
            provider=provider,
            registry=registry,
            permissions=permissions,
            model=model,
            instructions="You are a Delta agent.",
        ),
        provider,
    )


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


# -- matcher -------------------------------------------------------------------

def test_match_identity_classifies_validation_phrases():
    cases = {
        "你是谁": "product",
        "你是什么": "product",
        "介绍一下你自己": "product",
        "Who are you?": "product",
        "谁开发的": "developer",
        "谁做的Delta": "developer",
        "你是什么模型": "model",
        "什么模型驱动你": "model",
        "当前使用哪个模型": "model",
        "what model are you": "model",
    }
    for phrase, kind in cases.items():
        assert match_identity(phrase) == kind, phrase


def test_match_identity_is_conservative():
    # Normal questions that merely contain identity-ish tokens must fall through.
    for normal in [
        "你是怎么实现这个功能的",
        "你是什么时候发布的",
        "什么模型适合做翻译",
        "what model is best for coding",
        "介绍一下Delta的功能",
        "hi",
        "",
    ]:
        assert match_identity(normal) is None, normal


def test_answer_format():
    product = answer("product", "GLM-5.2 · Z AI")
    assert PRODUCT_NAME in product and DEVELOPER in product and "GLM-5.2" in product
    assert answer("developer", "x") == f"{PRODUCT_NAME} 由 {DEVELOPER} 打造。"
    assert answer("model", "GPT-5.5 · OpenAI") == "当前由 GPT-5.5 · OpenAI 模型驱动。"


def test_display_model_name_reuses_matrix_and_falls_back():
    assert display_model_name("zai:glm-5.2") == "GLM-5.2 · Z AI"
    assert display_model_name("not-a-known-model") == "not-a-known-model"
    assert display_model_name("") == ""


# -- engine interception -------------------------------------------------------


def test_identity_question_answered_locally_without_model_call(tmp_path):
    engine, provider = _engine(tmp_path, "zai:glm-5.2")
    events = _collect(engine, "你是谁")
    assert [e.type for e in events] == [
        EventType.TURN_START,
        EventType.ASSISTANT_DELTA,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    assert provider.calls == 0  # no model call
    text = next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE).data["text"]
    assert "Delta" in text and "Fongap Studio" in text and "GLM-5.2 · Z AI" in text
    # The user question and the local answer are both persisted.
    assert engine.messages[-1]["role"] == "assistant"
    assert engine.messages[-2]["role"] == "user"
    assert engine.messages[-2]["content"] == "你是谁"


def test_model_question_uses_live_model_after_switch(tmp_path):
    engine, _provider = _engine(tmp_path, "gpt-5.5")
    engine.switch_model("zai:glm-5.2")  # no history → returns None, but model is set
    events = _collect(engine, "什么模型驱动你")
    text = next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE).data["text"]
    assert "GLM-5.2 · Z AI" in text  # reflects the actual current model, not the old one


def test_normal_question_falls_through_to_model(tmp_path):
    engine, provider = _engine(tmp_path, "gpt-5.5")
    events = _collect(engine, "hello")
    assert provider.calls == 1
    assert EventType.ASSISTANT_MESSAGE in [e.type for e in events]
    assert next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE).data["text"] == "model reply"
