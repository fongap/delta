"""Unified product identity for Delta.

Single source of truth for the product name / developer / description, the
identity-question matcher, and the local (no-LLM) answer formatter. Identity-class
questions are answered locally so the underlying model never gets the chance to claim
a foreign product identity (ChatGPT / Claude / Gemini / GLM / MiniMax / …). The model
name in the answer always reflects the session's *actual* running model
(``TurnEngine.model``), resolved through the existing model-matrix display labels —
never guessed by the model, never hardcoded.

Identity hierarchy (never collapsed):
    Delta          = product
    Fongap Studio  = developer
    Model          = driving capability
    Provider       = infrastructure
"""

from __future__ import annotations

import re

PRODUCT_NAME = "Delta"
DEVELOPER = "Fongap Studio"
DESCRIPTION = "AI 工作助手"

# Appended to every agent's system prompt so that, even when a local identity
# intercept misses, the model still answers as Delta and does not impersonate the
# underlying LLM's vendor identity. Kept short on purpose.
IDENTITY_CLAUSE = (
    f"你的产品身份是 {PRODUCT_NAME}，由 {DEVELOPER} 打造。"
    "底层语言模型仅提供推理能力，不代表你的产品身份；被询问身份时以 Delta 身份回答，"
    "不要自称 ChatGPT、Claude、Gemini、GLM、MiniMax 等底层模型产品。"
)

# Strip trailing whitespace + terminal punctuation so "你是谁？" / "Who are you?" normalize
# to the bare question before the anchored match.
_TRAILING = re.compile(r"[。.?!？!…\s]+$")

# Identity patterns are anchored to the WHOLE normalized message (``^...$``) on purpose:
# a normal question that merely contains "你是" or "model" (e.g. "你是怎么实现的",
# "what model is best for coding") must fall through to the model. Order matters only
# for readability — the anchored alternatives are disjoint.
_PRODUCT = re.compile(
    r"^(你是谁|你是什么|你是什么东西|你是干嘛的|你是做什么的|"
    r"介绍一下你自己|自我介绍一下|自我介绍|"
    r"who are you|what are you|introduce yourself|tell me about yourself)$"
)
_DEVELOPER = re.compile(
    r"^(谁开发(了|的)?(你|delta)?|谁做(了|的)?(你|delta)?|谁创造(了|的)?你|"
    r"谁设计(了|的)?(你|delta)?|delta是谁(做|开发|设计)的|"
    r"who (made|created|developed|built) (you|delta))$"
)
_MODEL = re.compile(
    r"^(你(用|使用|用的|使用的)?什么模型|用的什么模型|什么模型驱动你|"
    r"当前是什么模型|当前(使用|用)哪个模型|当前(用的|使用的)?什么模型|"
    r"你是什么模型|你是(哪个|什么)模型|现在(用|使用)什么模型|"
    r"what model (are you|drives you|are you using|do you use)|"
    r"which model (are you|do you use|drives you))$"
)


def _normalize(text: str) -> str:
    return _TRAILING.sub("", text.strip()).lower()


def match_identity(text: str) -> str | None:
    """Classify a user message as an identity question, or ``None`` for normal input.

    Returns ``"product"`` | ``"developer"`` | ``"model"``. Conservative by design: only
    matches when the *whole* message is an identity question, so ordinary questions
    that happen to contain "你是" / "model" never trip the local path.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    s = _normalize(text)
    if not s:
        return None
    if _DEVELOPER.match(s):
        return "developer"
    if _MODEL.match(s):
        return "model"
    if _PRODUCT.match(s):
        return "product"
    return None


def display_model_name(model: str) -> str:
    """The human-facing name of the model actually driving the turn. Reuses the model
    matrix's display labels (the same source the GUI pickers read); falls back to the
    raw id for custom / non-matrix models. Never guessed, never hardcoded."""
    if not model:
        return ""
    from .providers.matrix import model_labels

    return model_labels().get(model, model)


def answer(kind: str, model_display: str) -> str:
    """Local identity answer (no model call). ``kind`` comes from :func:`match_identity`;
    ``model_display`` from :func:`display_model_name` on the live session model."""
    model = model_display or "当前配置的"
    if kind == "developer":
        return f"{PRODUCT_NAME} 由 {DEVELOPER} 打造。"
    if kind == "model":
        return f"当前由 {model} 模型驱动。"
    return (
        f"我是 {PRODUCT_NAME}，由 {DEVELOPER} 打造的 {DESCRIPTION}，"
        f"当前由 {model} 模型驱动。"
    )
