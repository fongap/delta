"""The curated model matrix — the only models we actively suggest, label, and vouch for.

Keyed by the FULL routed id, exactly as the ProviderRouter receives it — including reseller
"ugly names" like ``together:zai-org/GLM-5.2`` (bare ids route to the OpenAI default). Each
entry carries the UI display label and the model's capabilities, making this the single
source of truth the capability probe and the GUI's pickers read from.

Deliberately SMALL (owner call, 2026-07-04): current-generation, agent-capable (tool-calling)
models only. It is not user-editable — users can still add any custom model string, which
falls back to the conservative heuristics in ``capabilities.py`` at their own risk of
degraded results. Ids verified against vendor/reseller catalogs on 2026-07-04; refresh the
reseller rows when catalogs rotate (they rename on every model generation).

Context windows (``context_window``, tokens) feed the GUI's context-fill meter. Entries
where the vendor spec wasn't re-checked stay ``None`` — the meter simply hides rather than
showing a made-up denominator. Values entered 2026-07-28 from vendor docs; verify alongside
the id refresh.

Resellers: Together + Fireworks + OpenRouter. TODO: add Groq entries here AND its
descriptor in ``registry.py`` once the current provider surface is tested — deliberately
deferred to bound how much needs verifying at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from providers.base import ModelCapabilities

_AGENTIC = ModelCapabilities(
    tools=True, vision=False, parallel_tool_calls=True, streaming=True
)
# The native OpenAI and Anthropic implementations take PDFs directly; every
# OpenAI-compatible vendor and reseller in the matrix does not (their chat APIs have
# no inline file part — checked 2026-07-17), so those fall back via pdf_support.py.
_AGENTIC_VISION = ModelCapabilities(
    tools=True, vision=True, pdf=True, parallel_tool_calls=True, streaming=True
)


@dataclass(frozen=True)
class ModelEntry:
    label: str  # UI display name, e.g. "GLM-5.2 · via Together"
    caps: ModelCapabilities = _AGENTIC
    # Max context length in tokens (prompt side), for the GUI's context-fill meter.
    # None = not verified against the vendor spec yet; the meter hides.
    context_window: int | None = None


MATRIX: dict[str, ModelEntry] = {
    # -- first-party ------------------------------------------------------------
    # GPT-5.6 (2026-07-09): number = generation, Sol/Terra/Luna = capability tiers.
    # Bare "gpt-5.6" aliases to Sol server-side; we list the explicit tier ids only.
    # Rolling out — accounts without access get a friendly error (providers/errors.py).
    "gpt-5.6-sol": ModelEntry("GPT-5.6 Sol · OpenAI", _AGENTIC_VISION, 400_000),
    "gpt-5.6-terra": ModelEntry("GPT-5.6 Terra · OpenAI", _AGENTIC_VISION, 400_000),
    "gpt-5.6-luna": ModelEntry("GPT-5.6 Luna · OpenAI", _AGENTIC_VISION, 400_000),
    "gpt-5.5": ModelEntry("GPT-5.5 · OpenAI", _AGENTIC_VISION, 400_000),
    # Fable 5 (2026-06-09) is GA; its Mythos 5 sibling is approved-orgs-only, so it
    # stays out of a picker meant for the public.
    "anthropic:claude-fable-5": ModelEntry(
        "Claude Fable 5 · Anthropic", _AGENTIC_VISION, 1_000_000
    ),
    "anthropic:claude-opus-4-8": ModelEntry(
        "Claude Opus 4.8 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    "anthropic:claude-sonnet-4-6": ModelEntry(
        "Claude Sonnet 4.6 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    "anthropic:claude-haiku-4-5": ModelEntry(
        "Claude Haiku 4.5 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    # -- direct OpenAI-compatible vendors ----------------------------------------
    # Muse Spark (Meta Model API, public preview 2026-07-09): multimodal + tools via
    # their OpenAI-compat surface. Vision yes; PDFs unverified over compat — falls
    # back via pdf_support.py like the other compat vendors.
    "meta:muse-spark-1.1": ModelEntry(
        "Muse Spark 1.1 · Meta",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
    ),
    "zai:glm-5.2": ModelEntry("GLM-5.2 · Z AI", _AGENTIC, 128_000),
    "deepseek:deepseek-v4-flash": ModelEntry(
        "DeepSeek V4 Flash · DeepSeek", _AGENTIC, 128_000
    ),
    "deepseek:deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · DeepSeek", _AGENTIC, 128_000
    ),
    "kimi:kimi-k2.6": ModelEntry("Kimi K2.6 · Moonshot", _AGENTIC, 256_000),
    "minimax:MiniMax-M2.5": ModelEntry("MiniMax M2.5 · MiniMax"),
    "qwen:qwen3-max": ModelEntry("Qwen3 Max · Alibaba", _AGENTIC, 256_000),
    "xai:grok-4.3": ModelEntry("Grok 4.3 · xAI", _AGENTIC, 256_000),
    "mistral:mistral-large-latest": ModelEntry(
        "Mistral Large · Mistral", _AGENTIC, 128_000
    ),
    # -- resellers (their model namespaces, verbatim) -----------------------------
    "together:thinkingmachines/Inkling": ModelEntry("Inkling · via Together"),
    "together:zai-org/GLM-5.2": ModelEntry("GLM-5.2 · via Together", _AGENTIC, 128_000),
    # Kimi K3 on Together (landed late July 2026): 1M window, native vision; PDFs
    # unverified over the compat surface (falls back via pdf_support.py, like Muse Spark).
    "together:moonshotai/Kimi-K3": ModelEntry(
        "Kimi K3 · via Together",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        1_000_000,
    ),
    "together:moonshotai/Kimi-K2.7-Code": ModelEntry(
        "Kimi K2.7 Code · via Together", _AGENTIC, 256_000
    ),
    "together:moonshotai/Kimi-K2.6": ModelEntry(
        "Kimi K2.6 · via Together", _AGENTIC, 256_000
    ),
    "together:deepseek-ai/DeepSeek-V4-Pro": ModelEntry(
        "DeepSeek V4 Pro · via Together", _AGENTIC, 128_000
    ),
    "together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8": ModelEntry(
        "Llama 4 Maverick · via Together", _AGENTIC, 1_000_000
    ),
    "fireworks:accounts/fireworks/models/glm-5p2": ModelEntry(
        "GLM-5.2 · via Fireworks", _AGENTIC, 128_000
    ),
    "fireworks:accounts/fireworks/models/kimi-k2p6": ModelEntry(
        "Kimi K2.6 · via Fireworks", _AGENTIC, 256_000
    ),
    "fireworks:accounts/fireworks/models/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via Fireworks", _AGENTIC, 128_000
    ),
    "fireworks:accounts/fireworks/models/llama4-maverick-instruct-basic": ModelEntry(
        "Llama 4 Maverick · via Fireworks", _AGENTIC, 1_000_000
    ),
    # OpenRouter slugs are lowercase `<lab>/<model>` (checked against their catalog
    # 2026-07-25); same labs as above, one key for all of them.
    "openrouter:z-ai/glm-5.2": ModelEntry("GLM-5.2 · via OpenRouter", _AGENTIC, 128_000),
    "openrouter:moonshotai/kimi-k2.6": ModelEntry(
        "Kimi K2.6 · via OpenRouter", _AGENTIC, 256_000
    ),
    "openrouter:deepseek/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via OpenRouter", _AGENTIC, 128_000
    ),
    "openrouter:meta-llama/llama-4-maverick": ModelEntry(
        "Llama 4 Maverick · via OpenRouter", _AGENTIC, 1_000_000
    ),
}


def entry_for(model: str) -> ModelEntry | None:
    return MATRIX.get(model)


def model_labels() -> dict[str, str]:
    """Full-id → display-label map, shipped to the GUI so every picker shows human names."""
    return {mid: e.label for mid, e in MATRIX.items()}


def model_context_windows() -> dict[str, int]:
    """Full-id → context-window map (verified entries only), for the GUI's fill meter."""
    return {
        mid: e.context_window for mid, e in MATRIX.items() if e.context_window
    }


def models_for_provider(provider: str) -> list[str]:
    """BARE model ids (prefix stripped) the matrix curates for a provider — feeds the
    Settings pane's suggestions and the composer picker so both stay in lockstep with the
    matrix. OpenAI entries are stored without a prefix (bare ids route to the OpenAI
    default), so its list is every un-prefixed id."""
    if provider == "openai":
        return [mid for mid in MATRIX if ":" not in mid]
    prefix = provider + ":"
    return [mid[len(prefix) :] for mid in MATRIX if mid.startswith(prefix)]
