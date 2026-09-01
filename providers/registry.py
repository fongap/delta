"""Provider profiles, descriptors, discovery, and protocol-first client construction.

Delta supports exactly two runtime protocols: OpenAI-compatible and Anthropic Messages.
Vendor presets only provide friendly defaults; the stored profile protocol selects the
runtime implementation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from providers.anthropic_provider import AnthropicProvider
from providers.base import ProviderClient
from providers.endpoint import from_profile as endpoint_caps_from_profile
from providers.openai_provider import OpenAIProvider
from providers.openai_responses import OpenAIResponsesProvider

DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"

PROFILE_PROTOCOL_OPENAI = "openai"
PROFILE_PROTOCOL_ANTHROPIC = "anthropic"

CUSTOM_PROVIDERS: dict[str, dict[str, Any]] = {}

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,62}$")
_PROFILE_SECRET_PREFIX = "provider-profile:"


@dataclass(frozen=True)
class ProviderField:
    """One provider setting rendered by the desktop's dynamic form."""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""
    default: str = ""
    choices: tuple[dict[str, str], ...] = ()
    show_when: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "default": self.default,
            "choices": [dict(choice) for choice in self.choices],
            "show_when": self.show_when,
        }


@dataclass(frozen=True)
class ProviderDescriptor:
    """User-visible provider preset or custom profile descriptor."""

    name: str
    title: str
    needs_key: bool
    fields: list[ProviderField]
    build: Callable[[dict[str, Any], Any], ProviderClient] = field(repr=False)
    recommended_model: str | None = None
    env_key: str | None = None
    blurb: str = ""
    protocol: str = PROFILE_PROTOCOL_OPENAI
    preset: bool = True
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "needs_key": self.needs_key,
            "fields": [provider_field.to_dict() for provider_field in self.fields],
            "recommended_model": self.recommended_model,
            "blurb": self.blurb,
            "protocol": self.protocol,
            "preset": self.preset,
            "visible": self.visible,
        }


def _build_openai(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    base_url = str(profile.get("base_url") or DEFAULT_OPENAI_URL).strip().rstrip("/")
    api_key = str(profile.get("api_key") or "").strip() or None
    official = base_url == DEFAULT_OPENAI_URL
    mode = str(profile.get("api_mode") or "").strip().lower()
    if not mode:
        mode = "responses" if official else "chat"
    if mode == "responses":
        return OpenAIResponsesProvider(
            api_key=api_key,
            base_url=base_url,
            secrets=secrets if official else None,
            allow_credential_fallback=official,
        )
    return OpenAIProvider(
        api_key=api_key or "",
        base_url=base_url,
        secrets=secrets if official else None,
        allow_credential_fallback=official,
        endpoint_caps=endpoint_caps_from_profile(profile),
        endpoint_key=base_url,
    )


def _build_anthropic(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    from providers.anthropic_provider import DEFAULT_THINKING_BUDGET

    base_url = str(profile.get("base_url") or DEFAULT_ANTHROPIC_URL).strip().rstrip("/")
    api_key = str(profile.get("api_key") or "").strip() or None
    try:
        thinking_budget = int(str(profile.get("thinking_budget") or "").strip())
    except ValueError:
        thinking_budget = DEFAULT_THINKING_BUDGET
    official = base_url == DEFAULT_ANTHROPIC_URL
    return AnthropicProvider(
        api_key=api_key,
        base_url=base_url,
        secrets=secrets if official else None,
        allow_credential_fallback=official,
        thinking_budget=thinking_budget,
    )


def _openai_fields(
    *,
    base_url: str = "",
    key_label: str = "API key (optional)",
    key_required: bool = False,
) -> list[ProviderField]:
    return [
        ProviderField(
            "api_key",
            key_label,
            secret=True,
            required=key_required,
            placeholder="sk-…",
            help="Leave empty only when the endpoint does not require authentication.",
        ),
        ProviderField(
            "base_url",
            "Endpoint",
            default=base_url,
            placeholder=base_url or "https://…/v1",
            help="Base URL of an OpenAI-compatible API, including /v1 when required.",
        ),
    ]


def _anthropic_fields(*, base_url: str = "") -> list[ProviderField]:
    return [
        ProviderField(
            "api_key", "Anthropic API key", secret=True, placeholder="sk-ant-…"
        ),
        ProviderField(
            "base_url",
            "Endpoint",
            default=base_url,
            placeholder=base_url or "https://…",
            help="Base URL of an Anthropic Messages API.",
        ),
    ]


PROTOCOLS: dict[str, dict[str, Any]] = {
    PROFILE_PROTOCOL_OPENAI: {
        "title": "OpenAI",
        "needs_key": False,
        "fields": _openai_fields(),
        "build": _build_openai,
        "recommended_model": "gpt-4o",
    },
    PROFILE_PROTOCOL_ANTHROPIC: {
        "title": "Anthropic",
        "needs_key": True,
        "fields": _anthropic_fields(),
        "build": _build_anthropic,
        "recommended_model": "claude-fable-5",
    },
}


def _compat(
    name: str,
    title: str,
    *,
    base_url: str,
    recommended_model: str,
    env_key: str,
    endpoint_help: str = "",
) -> ProviderDescriptor:
    vendor = title.split(" (")[0]
    fields = _openai_fields(
        base_url=base_url,
        key_label=f"{vendor} API key",
        key_required=True,
    )
    fields[1] = ProviderField(
        "base_url",
        "Endpoint",
        required=False,
        default=base_url,
        placeholder=base_url,
        help=endpoint_help
        or f"Prefilled with {vendor}'s official endpoint; edit only for a regional or proxy variant.",
    )
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=fields,
        build=_build_openai,
        recommended_model=recommended_model,
        env_key=env_key,
        blurb=f"Uses {vendor}'s OpenAI-compatible API — the endpoint is prefilled, just add your key.",
        protocol=PROFILE_PROTOCOL_OPENAI,
    )


DESCRIPTORS: list[ProviderDescriptor] = [
    ProviderDescriptor(
        name="openai",
        title="OpenAI",
        needs_key=True,
        fields=[
            *_openai_fields(
                base_url=DEFAULT_OPENAI_URL,
                key_label="OpenAI API key",
                key_required=True,
            ),
            ProviderField(
                "api_mode",
                "API mode",
                required=False,
                default="responses",
                choices=(
                    {"value": "responses", "label": "Responses API"},
                    {"value": "chat", "label": "Chat Completions"},
                ),
                help="Responses is the default for OpenAI; compatible endpoints may use Chat Completions.",
            ),
        ],
        build=_build_openai,
        recommended_model="gpt-5.6-sol",
        env_key="OPENAI_API_KEY",
        protocol=PROFILE_PROTOCOL_OPENAI,
    ),
    ProviderDescriptor(
        name="anthropic",
        title="Claude (Anthropic)",
        needs_key=True,
        fields=_anthropic_fields(base_url=DEFAULT_ANTHROPIC_URL),
        build=_build_anthropic,
        recommended_model="claude-fable-5",
        env_key="ANTHROPIC_API_KEY",
        protocol=PROFILE_PROTOCOL_ANTHROPIC,
    ),
    _compat(
        "zai",
        "Z AI (GLM)",
        base_url="https://api.z.ai/api/paas/v4",
        recommended_model="glm-5.2",
        env_key="ZAI_API_KEY",
        endpoint_help="Prefilled with Z AI's international endpoint. China mainland: https://open.bigmodel.cn/api/paas/v4",
    ),
    _compat(
        "deepseek",
        "DeepSeek",
        base_url="https://api.deepseek.com",
        recommended_model="deepseek-v4-flash",
        env_key="DEEPSEEK_API_KEY",
    ),
    _compat(
        "kimi",
        "Kimi (Moonshot AI)",
        base_url="https://api.moonshot.ai/v1",
        recommended_model="kimi-k2.6",
        env_key="MOONSHOT_API_KEY",
        endpoint_help="Prefilled with Moonshot's international endpoint. China mainland: https://api.moonshot.cn/v1",
    ),
    _compat(
        "minimax",
        "MiniMax",
        base_url="https://api.minimax.io/v1",
        recommended_model="MiniMax-M2.5",
        env_key="MINIMAX_API_KEY",
    ),
    _compat(
        "qwen",
        "Qwen (Alibaba)",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        recommended_model="qwen3-max",
        env_key="DASHSCOPE_API_KEY",
        endpoint_help="Prefilled with Alibaba Model Studio's international endpoint. China: https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    _compat(
        "xai",
        "xAI (Grok)",
        base_url="https://api.x.ai/v1",
        recommended_model="grok-4.3",
        env_key="XAI_API_KEY",
    ),
    _compat(
        "mistral",
        "Mistral",
        base_url="https://api.mistral.ai/v1",
        recommended_model="mistral-large-latest",
        env_key="MISTRAL_API_KEY",
    ),
    _compat(
        "meta",
        "Meta (Muse Spark)",
        base_url="https://api.meta.ai/v1",
        recommended_model="muse-spark-1.1",
        env_key="META_API_KEY",
        endpoint_help="Prefilled with the Meta Model API endpoint.",
    ),
    _compat(
        "together",
        "Together AI",
        base_url="https://api.together.xyz/v1",
        recommended_model="zai-org/GLM-5.2",
        env_key="TOGETHER_API_KEY",
    ),
    _compat(
        "fireworks",
        "Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        recommended_model="accounts/fireworks/models/glm-5p2",
        env_key="FIREWORKS_API_KEY",
    ),
    _compat(
        "openrouter",
        "OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        recommended_model="z-ai/glm-5.2",
        env_key="OPENROUTER_API_KEY",
    ),
]

_BY_NAME = {descriptor.name: descriptor for descriptor in DESCRIPTORS}


def provider_profile_key(name: str) -> str:
    return _PROFILE_SECRET_PREFIX + name


def profile_protocol(name: str, profile: dict[str, Any] | None = None) -> str:
    configured = (profile or {}).get("protocol")
    if configured in (PROFILE_PROTOCOL_OPENAI, PROFILE_PROTOCOL_ANTHROPIC):
        return str(configured)
    descriptor = get_descriptor(name)
    return descriptor.protocol if descriptor else PROFILE_PROTOCOL_OPENAI


def _profile_defaults(
    name: str, profile: dict[str, Any], descriptor: ProviderDescriptor | None
) -> dict[str, Any]:
    prepared = dict(profile)
    if descriptor is not None:
        for provider_field in descriptor.fields:
            if provider_field.default:
                prepared.setdefault(provider_field.key, provider_field.default)
        if not prepared.get("api_key") and descriptor.env_key:
            env_key = os.environ.get(descriptor.env_key, "").strip()
            if env_key:
                prepared["api_key"] = env_key
    prepared.setdefault("name", name)
    prepared.setdefault(
        "protocol", descriptor.protocol if descriptor else PROFILE_PROTOCOL_OPENAI
    )
    if prepared["protocol"] == PROFILE_PROTOCOL_OPENAI:
        base_url = str(prepared.get("base_url") or "").strip().rstrip("/")
        official = base_url in ("", DEFAULT_OPENAI_URL)
        if name == "openai" and not official:
            prepared["api_mode"] = "chat"
        else:
            prepared.setdefault("api_mode", "responses" if name == "openai" else "chat")
    return prepared


def migrate_legacy_provider_profiles(secrets: Any, prefs: dict[str, Any]) -> dict[str, Any]:
    """Move the old `provider:<name>` records into endpoint-bound profiles once."""

    marker = prefs.get("provider_profile_migration")
    if isinstance(marker, dict) and marker.get("version") == 1:
        return marker
    custom = prefs.get("custom_providers") or {}
    profiles = dict(prefs.get("provider_profiles") or {})
    names = set(_BY_NAME)
    if isinstance(custom, dict):
        names.update(custom)
    migrated: list[str] = []
    skipped: list[str] = []
    for name in sorted(names):
        meta = custom.get(name) if isinstance(custom, dict) else None
        descriptor = _BY_NAME.get(name)
        protocol = (
            meta.get("protocol")
            if isinstance(meta, dict)
            else (descriptor.protocol if descriptor is not None else None)
        )
        if protocol not in (PROFILE_PROTOCOL_OPENAI, PROFILE_PROTOCOL_ANTHROPIC):
            continue
        legacy_key = f"provider:{name}"
        target_key = provider_profile_key(name)
        legacy = secrets.get(legacy_key)
        existing = secrets.get(target_key)
        if legacy and not existing:
            target = _profile_defaults(name, dict(legacy), _BY_NAME.get(name))
            target["protocol"] = protocol
            secrets.put(target_key, target)
            secrets.delete(legacy_key)
            migrated.append(name)
        elif legacy and existing:
            secrets.delete(legacy_key)
            skipped.append(name)
        if isinstance(custom, dict) and name in custom:
            profiles[name] = {"protocol": protocol, "preset": False}
    if custom:
        prefs.pop("custom_providers", None)
    if profiles:
        prefs["provider_profiles"] = profiles
    receipt = {"version": 1, "migrated": migrated, "duplicates_removed": skipped}
    prefs["provider_profile_migration"] = receipt
    return receipt


def provider_descriptors() -> list[ProviderDescriptor]:
    return [
        descriptor
        for descriptor in [*DESCRIPTORS, *custom_provider_descriptors()]
        if descriptor.visible
    ]


def provider_names() -> list[str]:
    return [descriptor.name for descriptor in provider_descriptors()]


def custom_provider_names() -> list[str]:
    return list(CUSTOM_PROVIDERS)


def is_custom_provider(name: str) -> bool:
    return name in CUSTOM_PROVIDERS


def get_descriptor(name: str) -> ProviderDescriptor | None:
    return _BY_NAME.get(name) or _custom_descriptor(name)


def core_protocol_descriptors() -> dict[str, dict[str, Any]]:
    return {
        protocol: {
            "title": metadata["title"],
            "needs_key": metadata["needs_key"],
            "fields": list(metadata["fields"]),
            "recommended_model": metadata["recommended_model"],
        }
        for protocol, metadata in PROTOCOLS.items()
    }


def _custom_descriptor(name: str) -> ProviderDescriptor | None:
    meta = CUSTOM_PROVIDERS.get(name)
    if not meta:
        return None
    protocol = str(meta["protocol"])
    protocol_meta = PROTOCOLS[protocol]
    return ProviderDescriptor(
        name=name,
        title=name,
        needs_key=bool(protocol_meta["needs_key"]),
        fields=list(protocol_meta["fields"]),
        build=protocol_meta["build"],
        recommended_model=str(protocol_meta["recommended_model"]),
        blurb=str(protocol_meta["title"]),
        protocol=protocol,
        preset=False,
    )


def custom_provider_descriptors() -> list[ProviderDescriptor]:
    return [
        descriptor
        for name in CUSTOM_PROVIDERS
        if (descriptor := _custom_descriptor(name)) is not None
    ]


def register_custom_provider(
    alias: str, protocol: str, fields_meta: dict[str, Any] | None = None
) -> None:
    if protocol not in (PROFILE_PROTOCOL_OPENAI, PROFILE_PROTOCOL_ANTHROPIC):
        raise ValueError(f"Unknown protocol: {protocol}")
    if not _valid_alias(alias):
        raise ValueError("Invalid provider alias.")
    CUSTOM_PROVIDERS[alias] = {"protocol": protocol, **(fields_meta or {})}


def unregister_custom_provider(alias: str) -> None:
    CUSTOM_PROVIDERS.pop(alias, None)


def _valid_alias(alias: str) -> bool:
    return bool(alias and _ALIAS_RE.fullmatch(alias))


def build_provider_client(
    name: str, profile: dict[str, Any], secrets: Any
) -> ProviderClient:
    """Build only from the profile protocol; provider identity supplies defaults, not code."""

    descriptor = get_descriptor(name)
    prepared = _profile_defaults(name, profile or {}, descriptor)
    protocol = profile_protocol(name, prepared)
    if protocol == PROFILE_PROTOCOL_ANTHROPIC:
        return _build_anthropic(prepared, secrets)
    return _build_openai(prepared, secrets)


def descriptor_configured(
    descriptor: ProviderDescriptor, profile: dict[str, Any]
) -> bool:
    if not descriptor.needs_key:
        return all(
            profile.get(provider_field.key) or provider_field.default
            for provider_field in descriptor.fields
            if provider_field.required
        )
    if any(provider_field.key == "api_key" for provider_field in descriptor.fields):
        return bool(profile.get("api_key")) or bool(
            descriptor.env_key and os.environ.get(descriptor.env_key)
        )
    return all(
        profile.get(provider_field.key) or provider_field.default
        for provider_field in descriptor.fields
        if provider_field.required
    )


def detect_provider(api_key: str) -> str | None:
    key = (api_key or "").strip()
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith(("sk-", "sk_")):
        return "openai"
    return None


def _request_provider_models(
    protocol: str,
    *,
    api_key: str,
    base_url: str,
    timeout: float,
) -> Any:
    import httpx

    if protocol == PROFILE_PROTOCOL_ANTHROPIC:
        return httpx.get(
            base_url.rstrip("/") + "/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
        )
    return httpx.get(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=timeout,
    )


def verify_provider_key(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    fields: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    descriptor = get_descriptor(name) or _BY_NAME["openai"]
    prepared = _profile_defaults(name, fields or {}, descriptor)
    protocol = profile_protocol(name, prepared)
    key = (api_key or str(prepared.get("api_key") or "")).strip()
    endpoint = (
        base_url
        or str(prepared.get("base_url") or "")
        or (
            DEFAULT_ANTHROPIC_URL
            if protocol == PROFILE_PROTOCOL_ANTHROPIC
            else DEFAULT_OPENAI_URL
        )
    ).strip()
    try:
        response = _request_provider_models(
            protocol,
            api_key=key,
            base_url=endpoint,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Couldn't reach {descriptor.title} ({exc.__class__.__name__}).",
        }
    if response.status_code < 300:
        return {"ok": True}
    if response.status_code in (401, 403):
        return {"ok": False, "error": "Invalid API key."}
    if response.status_code == 404:
        return {
            "ok": False,
            "error": "Model listing is not supported by this endpoint — add a model ID manually.",
        }
    return {
        "ok": False,
        "error": f"{descriptor.title} returned HTTP {response.status_code}.",
    }


def fetch_provider_models(
    name: str, profile: dict[str, Any], secrets: Any, timeout: float = 10.0
) -> dict[str, Any]:
    descriptor = get_descriptor(name)
    if descriptor is None:
        return {"ok": False, "error": f"Unknown provider: {name}."}
    prepared = _profile_defaults(name, profile or {}, descriptor)
    protocol = profile_protocol(name, prepared)
    api_key = str(prepared.get("api_key") or "").strip()
    base_url = str(
        prepared.get("base_url")
        or (
            DEFAULT_ANTHROPIC_URL
            if protocol == PROFILE_PROTOCOL_ANTHROPIC
            else DEFAULT_OPENAI_URL
        )
    ).strip()
    try:
        response = _request_provider_models(
            protocol,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Couldn't reach {descriptor.title} ({exc.__class__.__name__}).",
        }
    if response.status_code < 300:
        payload = response.json()
        model_ids = [
            model["id"]
            for model in payload.get("data", [])
            if isinstance(model, dict) and isinstance(model.get("id"), str)
        ]
        return {"ok": True, "models": model_ids}
    if response.status_code in (401, 403):
        return {"ok": False, "error": "Invalid API key."}
    if response.status_code == 404:
        return {
            "ok": False,
            "error": "Model listing is not supported by this endpoint — add a model ID manually.",
        }
    return {
        "ok": False,
        "error": f"{descriptor.title} returned HTTP {response.status_code}.",
    }
