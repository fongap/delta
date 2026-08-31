"""Model-provider registry — descriptors + a factory, mirroring the connector
(`connectors/descriptors.py`) and web-search (`web/providers.py`) patterns.

A `ProviderDescriptor` declares a provider's UI config `fields` (rendered dynamically by the
GUI, same `to_dict()` shape connectors use) and a `build(profile, secrets)` factory that returns
a `ProviderClient`. The `ProviderRouter` selects a descriptor by the `provider:` prefix of a
model string and builds (and caches) its client from the matching SecretStore profile.

Today: `openai` (the default — native models via the Responses API; an optional custom
endpoint covering Azure OpenAI's `/openai/v1` and any OpenAI-compliant gateway keeps the
Chat Completions path), `anthropic` (native Messages API via
`AnthropicProvider`), `gemini` (native Google GenAI API via `GeminiProvider`), `bedrock`
(models in the user's own AWS account — Claude natively, everything else via Converse),
`vertex` (the user's own GCP project — Gemini and Claude natively, open-weight via the
MaaS endpoint), and `ollama` (local, OpenAI-compatible `/v1`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional

from providers.anthropic_provider import AnthropicProvider
from providers.base import ProviderClient
from providers.bedrock_provider import BedrockProvider
from providers.endpoint import from_profile as endpoint_caps_from_profile
from providers.gemini_provider import GeminiProvider
from providers.openai_provider import OpenAIProvider
from providers.openai_responses import OpenAIResponsesProvider
from providers.vertex_provider import VertexProvider

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"
DEFAULT_GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# These are the only first-class remote protocols. Vendor names are profile presets that choose
# a default endpoint/model; they do not select a distinct client implementation.
PROFILE_PROTOCOL_OPENAI = "openai"
PROFILE_PROTOCOL_ANTHROPIC = "anthropic"

# Protocols a custom provider can be built against. Each entry reuses the
# protocol-specific builder logic (so native endpoints/behaviour match the
# built-in descriptors) but renders only the minimal field set a user can
# fill in for an arbitrary alias. Key resolution stays inside each builder.
PROTOCOL_OPENAI_COMPAT = "openai-compatible"  # default: a self-hosted gateway / Azure / etc.
PROTOCOL_OPENAI_NATIVE = "openai-native"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_GEMINI = "gemini"
PROTOCOL_OLLAMA = "ollama"
PROTOCOL_BEDROCK = "bedrock"
PROTOCOL_VERTEX = "vertex"

# Dynamic user-registered custom providers (alias -> {protocol, fields_meta}).
# Bootstrapped from persistent prefs by SessionManager; consulted so arbitrary
# user aliases route like any built-in provider.
CUSTOM_PROVIDERS: dict[str, dict[str, Any]] = {}

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,62}$")


@dataclass(frozen=True)
class ProviderField:
    """One config input for a provider, rendered by the GUI (mirrors connectors' `Field`)."""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""
    # Pre-filled (still editable) form value — e.g. an OpenAI-compatible vendor's official
    # endpoint, so the user only has to paste a key. Distinct from `placeholder` (grey hint).
    default: str = ""
    # Non-empty → the field renders as a segmented choice control instead of a text input;
    # each option is {"value", "label"} plus optional UI extras: "tag" (a tiny badge like
    # "Easiest"), "desc" (one-liner atop the method's panel), and "command" (a copyable
    # terminal command shown in the panel, e.g. the gcloud ADC login). The chosen value is
    # stored like any other field value.
    choices: tuple = ()
    # {"other_field_key": "value"} → the field only renders while that other field holds
    # that value. Drives auth-method switching (Bedrock) without a per-provider form.
    show_when: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "default": self.default,
            "choices": [dict(c) for c in self.choices],
            "show_when": self.show_when,
        }


@dataclass(frozen=True)
class ProviderDescriptor:
    """A model provider: its UI fields + a factory that builds its `ProviderClient`."""

    name: str
    title: str
    needs_key: bool
    fields: list[ProviderField]
    build: Callable[[dict[str, Any], Any], ProviderClient] = field(repr=False)
    recommended_model: str | None = (
        None  # pre-filled in the UI; auto-added on configure
    )
    env_key: str | None = (
        None  # env var that can supply the API key (e.g. ANTHROPIC_API_KEY)
    )
    # One-line note under the provider title (e.g. "Connects through X's OpenAI-compatible API").
    blurb: str = ""
    # Protocol is the runtime boundary; `name` is only a profile/preset identity used in the UI
    # and in model prefixes for backwards compatibility.
    protocol: str = PROFILE_PROTOCOL_OPENAI
    preset: bool = True
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "needs_key": self.needs_key,
            "fields": [f.to_dict() for f in self.fields],
            "recommended_model": self.recommended_model,
            "blurb": self.blurb,
            "protocol": self.protocol,
            "preset": self.preset,
            "visible": self.visible,
        }


def _normalize_ollama_url(url: str | None) -> str:
    """Accept `http://host:11434` or `.../v1` and return an OpenAI-compatible base URL.

    Ollama serves its OpenAI-compatible API under `/v1`; the native API lives at the root, so we
    always target `<root>/v1`.
    """
    base = (url or DEFAULT_OLLAMA_URL).strip().rstrip("/")
    if not base:
        base = DEFAULT_OLLAMA_URL
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base


def _build_openai(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Key resolution stays in resolve_api_key (explicit → env → SecretStore), so we just
    # hand over the SecretStore. Stock OpenAI (no custom endpoint) speaks the Responses
    # API — the only wire with reasoning + tools on GPT-5.6+. A custom endpoint (Azure
    # OpenAI /openai/v1, vLLM, any OpenAI-compliant gateway) keeps Chat Completions,
    # which is what compat servers implement.
    p = profile or {}
    base_url = ((p.get("base_url") or "").strip().rstrip("/")) or DEFAULT_OPENAI_URL
    api_key = (p.get("api_key") or "").strip() or None
    mode = (p.get("api_mode") or ("responses" if base_url == DEFAULT_OPENAI_URL else "chat")).strip().lower()
    # Environment fallback is only legitimate for the official endpoint. A custom URL is an
    # independent credential boundary even when its field was left blank in the form.
    official = base_url == DEFAULT_OPENAI_URL
    if mode == "chat":
        # Custom endpoints get the capability profile (providers/endpoint.py): params
        # this server is known to reject are never sent. Keyed by base_url for learning.
        return OpenAIProvider(
            api_key=api_key,
            base_url=base_url,
            secrets=secrets if official else None,
            allow_credential_fallback=official,
            endpoint_caps=endpoint_caps_from_profile(profile),
            endpoint_key=base_url,
        )
    return OpenAIResponsesProvider(
        api_key=api_key,
        base_url=base_url,
        secrets=secrets if official else None,
        allow_credential_fallback=official,
    )


def _build_openai_native(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    return _build_openai({**(profile or {}), "api_mode": "responses"}, secrets)


def _build_openai_compat(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Custom OpenAI-compatible endpoint. Key resolved from the alias's OWN profile (never the
    # OpenAI env/SecretStore fallback), so a configured OpenAI key is never silently sent to a
    # different endpoint. Missing key ⇒ fail fast with a clear error at first use.
    base_url = ((profile or {}).get("base_url") or "").strip()
    api_key = ((profile or {}).get("api_key") or "").strip()
    if not base_url:
        raise RuntimeError(
            "No server address configured — add it in Settings ▸ Models."
        )
    return OpenAIProvider(
        api_key=api_key,
        base_url=base_url,
        allow_credential_fallback=False,
        endpoint_caps=endpoint_caps_from_profile(profile),
        endpoint_key=base_url,
    )


def _build_anthropic(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Key resolution stays in AnthropicProvider/resolve_api_key (explicit → env → SecretStore),
    # deferred to first call so the provider can be built before a key exists.
    # thinking_budget: hidden profile override — absent/invalid → the default (ON),
    # explicit 0 → off (see DEFAULT_THINKING_BUDGET).
    from providers.anthropic_provider import DEFAULT_THINKING_BUDGET

    base_url = (((profile or {}).get("base_url") or "").strip().rstrip("/")) or DEFAULT_ANTHROPIC_URL
    api_key = ((profile or {}).get("api_key") or "").strip() or None
    try:
        thinking_budget = int(str((profile or {}).get("thinking_budget") or "").strip())
    except ValueError:
        thinking_budget = DEFAULT_THINKING_BUDGET
    return AnthropicProvider(
        api_key=api_key,
        base_url=base_url,
        secrets=secrets if base_url == DEFAULT_ANTHROPIC_URL else None,
        allow_credential_fallback=base_url == DEFAULT_ANTHROPIC_URL,
        thinking_budget=thinking_budget,
    )


def _build_gemini(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Gemini's public OpenAI-compatible endpoint keeps the core to two protocols. Unlike the
    # former native SDK path, it is endpoint-bound and never falls back to another profile key.
    p = {**(profile or {}), "base_url": (profile or {}).get("base_url") or DEFAULT_GEMINI_OPENAI_URL}
    return _build_openai_compat(p, secrets)


def _build_bedrock(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Credentials resolve inside boto3/AnthropicBedrock at call time: explicit keys →
    # named profile → ambient chain (env / ~/.aws default / instance role).
    p = profile or {}

    def get(key: str) -> str | None:
        return (p.get(key) or "").strip() or None

    return BedrockProvider(
        region=get("region"),
        auth_method=get("auth_method"),
        bedrock_api_key=get("bedrock_api_key"),
        profile_name=get("aws_profile"),
        access_key_id=get("aws_access_key_id"),
        secret_access_key=get("aws_secret_access_key"),
        session_token=get("aws_session_token"),
    )


def _build_vertex(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    p = profile or {}

    def get(key: str) -> str | None:
        return (p.get(key) or "").strip() or None

    return VertexProvider(
        project=get("project"),
        location=get("location"),
        auth_method=get("auth_method"),
        service_account_json=get("service_account_json"),
        api_key=get("vertex_api_key"),
    )


def _build_ollama(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Ollama's OpenAI-compatible endpoint ignores the key but the SDK requires a non-empty
    # string, so we pass a placeholder. `base_url` comes from the stored profile (or the default).
    base_url = _normalize_ollama_url((profile or {}).get("base_url"))
    return OpenAIProvider(
        api_key="ollama",
        base_url=base_url,
        allow_credential_fallback=False,
        endpoint_caps=endpoint_caps_from_profile(profile),
        endpoint_key=base_url,
    )


def _openai_compat(vendor: str, default_base_url: str, env_key: str | None = None):
    """Builder factory for vendors reached through their OpenAI-compatible API (Z AI, DeepSeek,
    Kimi, MiniMax, Qwen, xAI, Mistral). The key is resolved from the vendor's OWN profile (or its
    env var) — deliberately NOT from the OpenAI env/SecretStore fallback, so a configured OpenAI
    key is never silently sent to a different vendor's endpoint. Missing key ⇒ fail fast with a
    vendor-named error (these are only built on demand, when one of their models is selected).
    """

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        base_url = ((profile or {}).get("base_url") or "").strip() or default_base_url
        api_key = ((profile or {}).get("api_key") or "").strip() or (
            os.environ.get(env_key, "").strip() if env_key else ""
        )
        if not api_key:
            raise RuntimeError(
                f"No {vendor} API key configured — add it in Settings ▸ Models."
            )
        return OpenAIProvider(
            api_key=api_key,
            base_url=base_url,
            endpoint_caps=endpoint_caps_from_profile(profile),
            endpoint_key=base_url,
        )

    return build


def _compat(
    name: str,
    title: str,
    *,
    base_url: str,
    recommended_model: str,
    env_key: str,
    endpoint_help: str = "",
) -> ProviderDescriptor:
    """Descriptor for an OpenAI-compatible vendor: key + a prefilled, editable endpoint."""
    vendor = title.split(" (")[0]
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                f"{vendor} API key",
                secret=True,
            ),
            ProviderField(
                "base_url",
                "Endpoint",
                required=False,
                default=base_url,
                placeholder=base_url,
                help=endpoint_help
                or f"Prefilled with {vendor}'s official endpoint; edit only for a regional or proxy variant.",
            ),
        ],
        build=_openai_compat(vendor, base_url, env_key),
        recommended_model=recommended_model,
        env_key=env_key,
        blurb=f"Uses {vendor}'s OpenAI-compatible API — the endpoint is prefilled, just add your key.",
    )


PROTOCOLS: dict[str, dict[str, Any]] = {
    PROTOCOL_OPENAI_COMPAT: {
        "title": "OpenAI compatible",
        # Key OPTIONAL: local servers (LM Studio, vLLM, llama.cpp, Ollama's OpenAI
        # endpoint) typically run without auth; cloud endpoints just 401 on a wrong/absent
        # key, which surfaces as the normal verify/fetch error.
        "needs_key": False,
        "fields": [
            ProviderField(
                "api_key", "API key (optional)", secret=True, required=False,
                placeholder="sk-…",
                help="Leave empty for local servers that don't need authentication.",
            ),
            ProviderField(
                "base_url", "Server address", secret=False, placeholder="https://…/v1",
                help="Base URL of an OpenAI-compatible API. Include /v1 if your endpoint uses it.",
            ),
        ],
        "build": _build_openai_compat,
        "recommended_model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
    },
    PROTOCOL_OPENAI_NATIVE: {
        "title": "OpenAI",
        "needs_key": True,
        "fields": [
            ProviderField("api_key", "OpenAI API key", secret=True, placeholder="sk-…"),
        ],
        "build": _build_openai_native,
        "recommended_model": "gpt-5.6-sol",
        "env_key": "OPENAI_API_KEY",
    },
    PROTOCOL_ANTHROPIC: {
        "title": "Anthropic",
        "needs_key": True,
        "fields": [ProviderField("api_key", "Anthropic API key", secret=True, placeholder="sk-ant-…")],
        "build": _build_anthropic,
        "recommended_model": "claude-fable-5",
        "env_key": "ANTHROPIC_API_KEY",
    },
    PROTOCOL_GEMINI: {
        "title": "Gemini",
        "needs_key": True,
        "fields": [ProviderField("api_key", "Gemini API key", secret=True, placeholder="AIza…")],
        "build": _build_gemini,
        "recommended_model": "gemini-3.6-flash",
        "env_key": "GEMINI_API_KEY",
    },
    PROTOCOL_OLLAMA: {
        "title": "Ollama (local models)",
        "needs_key": False,
        "fields": [
            ProviderField(
                "base_url", "Ollama server URL", secret=False, placeholder=DEFAULT_OLLAMA_URL,
                help="Where `ollama serve` is listening. The OpenAI-compatible /v1 path is added automatically.",
            ),
        ],
        "build": _build_ollama,
        "recommended_model": "qwen3-coder:30b",
    },
    PROTOCOL_BEDROCK: {
        "title": "AWS Bedrock",
        "needs_key": True,
        "fields": [
            ProviderField("region", "AWS region", secret=False, placeholder="us-east-1",
                          help="The region your Bedrock model access is enabled in."),
            ProviderField(
                "auth_method", "Connect with", secret=False, required=False, default="api_key",
                choices=(
                    {"value": "api_key", "label": "Bedrock API key", "tag": "Easiest",
                     "desc": "A single key generated on the Bedrock console — no AWS CLI or IAM setup needed."},
                    {"value": "profile", "label": "AWS profile",
                     "desc": "Uses a named profile from ~/.aws — works with `aws configure` and `aws sso login`."},
                    {"value": "iam", "label": "IAM keys",
                     "desc": "An IAM access key pair. For temporary STS credentials, include the session token."},
                ),
            ),
            ProviderField("bedrock_api_key", "Bedrock API key", secret=True, required=False,
                          placeholder="ABSK…", show_when={"auth_method": "api_key"}),
            ProviderField("aws_profile", "AWS profile", secret=False, required=False, placeholder="default",
                          show_when={"auth_method": "profile"},
                          help="Leave blank to use your default AWS credentials (env vars or ~/.aws)."),
            ProviderField("aws_access_key_id", "Access key ID", secret=False, required=False, placeholder="AKIA…",
                          show_when={"auth_method": "iam"}),
            ProviderField("aws_secret_access_key", "Secret access key", secret=True, required=False,
                          show_when={"auth_method": "iam"}),
            ProviderField("aws_session_token", "Session token (STS only, optional)", secret=True, required=False,
                          show_when={"auth_method": "iam"}),
        ],
        "build": _build_bedrock,
        "recommended_model": "claude/anthropic.claude-sonnet-4-6-v1:0",
    },
    PROTOCOL_VERTEX: {
        "title": "Vertex AI (Google Cloud)",
        "needs_key": True,
        "fields": [
            ProviderField("project", "GCP project ID", secret=False, placeholder="my-project-123"),
            ProviderField("location", "Location", secret=False, placeholder="global",
                          help="Use `global` for the newest Gemini and Claude models."),
            ProviderField(
                "auth_method", "Connect with", secret=False, required=False, default="adc",
                choices=(
                    {"value": "adc", "label": "Google Cloud login", "tag": "Recommended",
                     "desc": "Uses your machine's Google Cloud identity (Application Default Credentials). Nothing to paste — sign in once in a terminal:",
                     "command": "gcloud auth application-default login"},
                    {"value": "service_account", "label": "Service account",
                     "desc": "A service-account key — the usual path on shared or headless machines."},
                    {"value": "api_key", "label": "API key",
                     "desc": "A long-lived key from the Google Cloud console's API Keys page. Reaches Gemini models only."},
                ),
            ),
            ProviderField("service_account_json", "Service-account JSON", secret=True, required=False,
                          show_when={"auth_method": "service_account"}, help="Paste the JSON key, or a path to the file."),
            ProviderField("vertex_api_key", "Vertex API key", secret=True, required=False, placeholder="AQ.…",
                          show_when={"auth_method": "api_key"}),
        ],
        "build": _build_vertex,
        "recommended_model": "gemini/gemini-3.6-flash",
    },
}


DESCRIPTORS: list[ProviderDescriptor] = [
    ProviderDescriptor(
        name="openai",
        title="OpenAI",
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                "OpenAI API key",
                secret=True,
                placeholder="sk-…",
            ),
            ProviderField(
                "base_url",
                "Endpoint",
                secret=False,
                default=DEFAULT_OPENAI_URL,
                placeholder=DEFAULT_OPENAI_URL,
                help="Official OpenAI endpoint by default. Edit it for a compatible gateway; its key stays bound to this endpoint.",
            ),
            ProviderField(
                "api_mode",
                "API mode",
                secret=False,
                required=False,
                default="responses",
                choices=(
                    {"value": "responses", "label": "Responses API"},
                    {"value": "chat", "label": "Chat Completions"},
                ),
                help="Use Responses for OpenAI; use Chat Completions only for a compatible endpoint that does not implement Responses.",
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
        fields=[
            ProviderField(
                "api_key",
                "Anthropic API key",
                secret=True,
                placeholder="sk-ant-…",
            ),
            ProviderField(
                "base_url",
                "Endpoint",
                secret=False,
                default=DEFAULT_ANTHROPIC_URL,
                placeholder=DEFAULT_ANTHROPIC_URL,
                help="Official Anthropic Messages endpoint by default. A custom endpoint uses only this profile's key.",
            ),
            # No thinking_budget field (owner call 2026-07-23): extended thinking is
            # on by default; the profile key stays a hidden override (0 = off).
        ],
        build=_build_anthropic,
        recommended_model="claude-fable-5",
        env_key="ANTHROPIC_API_KEY",
        protocol=PROFILE_PROTOCOL_ANTHROPIC,
    ),
    ProviderDescriptor(
        name="gemini",
        title="Gemini (Google preset)",
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                "Google AI API key",
                secret=True,
                placeholder="AIza…",
            ),
            ProviderField(
                "base_url",
                "OpenAI-compatible endpoint",
                secret=False,
                default=DEFAULT_GEMINI_OPENAI_URL,
                placeholder=DEFAULT_GEMINI_OPENAI_URL,
                help="Google's OpenAI-compatible endpoint. Edit only for a controlled proxy.",
            ),
        ],
        build=_build_gemini,
        recommended_model="gemini-3.6-flash",
        protocol=PROFILE_PROTOCOL_OPENAI,
        blurb="Preset: Google Gemini through its OpenAI-compatible endpoint.",
    ),
    ProviderDescriptor(
        name="bedrock",
        title="AWS Bedrock",
        needs_key=True,
        fields=[
            ProviderField(
                "region",
                "AWS region",
                secret=False,
                placeholder="us-east-1",
                help="The region your Bedrock model access is enabled in.",
            ),
            # One auth method at a time (owner call 2026-07-26): AWS users are advanced —
            # a direct choice beats a pile of "(optional)" fields with hidden precedence.
            ProviderField(
                "auth_method",
                "Connect with",
                secret=False,
                required=False,  # the default stands in; builder tolerates absence
                default="api_key",
                choices=(
                    {
                        "value": "api_key",
                        "label": "Bedrock API key",
                        "tag": "Easiest",
                        "desc": "A single key generated on the Bedrock console — no AWS CLI or IAM setup needed.",
                    },
                    {
                        "value": "profile",
                        "label": "AWS profile",
                        "desc": "Uses a named profile from ~/.aws — works with `aws configure` and `aws sso login`.",
                    },
                    {
                        "value": "iam",
                        "label": "IAM keys",
                        "desc": "An IAM access key pair. For temporary STS credentials, include the session token.",
                    },
                ),
            ),
            ProviderField(
                "bedrock_api_key",
                "Bedrock API key",
                secret=True,
                required=False,
                placeholder="ABSK…",
                show_when={"auth_method": "api_key"},
            ),
            ProviderField(
                "aws_profile",
                "AWS profile",
                secret=False,
                required=False,
                placeholder="default",
                show_when={"auth_method": "profile"},
                help="Leave blank to use your default AWS credentials (env vars or ~/.aws).",
            ),
            ProviderField(
                "aws_access_key_id",
                "Access key ID",
                secret=False,
                required=False,
                placeholder="AKIA…",
                show_when={"auth_method": "iam"},
            ),
            ProviderField(
                "aws_secret_access_key",
                "Secret access key",
                secret=True,
                required=False,
                show_when={"auth_method": "iam"},
            ),
            ProviderField(
                "aws_session_token",
                "Session token (STS only, optional)",
                secret=True,
                required=False,
                show_when={"auth_method": "iam"},
            ),
        ],
        build=_build_bedrock,
        recommended_model="claude/anthropic.claude-sonnet-4-6-v1:0",
        blurb="Runs models inside your own AWS account. Claude uses Anthropic's native "
        "Bedrock path; every other model goes through the Converse API.",
        visible=False,
    ),
    ProviderDescriptor(
        name="vertex",
        title="Vertex AI (Google Cloud)",
        needs_key=True,
        fields=[
            ProviderField(
                "project",
                "GCP project ID",
                secret=False,
                placeholder="my-project-123",
            ),
            ProviderField(
                "location",
                "Location",
                secret=False,
                placeholder="global",
                help="Use `global` for the newest Gemini and Claude models. Some models "
                "are regional — Model Garden lists each (Claude also: us-east5 / "
                "europe-west1; Qwen3 Coder: us-south1).",
            ),
            ProviderField(
                "auth_method",
                "Connect with",
                secret=False,
                required=False,  # the default stands in; builder tolerates absence
                default="adc",
                choices=(
                    {
                        "value": "adc",
                        "label": "Google Cloud login",
                        "tag": "Recommended",
                        "desc": "Uses your machine's Google Cloud identity (Application "
                        "Default Credentials). Nothing to paste — sign in once in a terminal:",
                        "command": "gcloud auth application-default login",
                    },
                    {
                        "value": "service_account",
                        "label": "Service account",
                        "desc": "A service-account key — the usual path on shared or headless machines.",
                    },
                    {
                        "value": "api_key",
                        "label": "API key",
                        "desc": "A long-lived key from the Google Cloud console's API Keys page. "
                        "Reaches Gemini models only — Claude and open-weight need Google "
                        "Cloud login or a service account.",
                    },
                ),
            ),
            ProviderField(
                "service_account_json",
                "Service-account JSON",
                secret=True,
                required=False,
                show_when={"auth_method": "service_account"},
                help="Paste the JSON key, or a path to the file.",
            ),
            ProviderField(
                "vertex_api_key",
                "Vertex API key",
                secret=True,
                required=False,
                placeholder="AQ.…",
                show_when={"auth_method": "api_key"},
            ),
        ],
        build=_build_vertex,
        recommended_model="gemini/gemini-3.6-flash",
        blurb="Runs models inside your own Google Cloud project. Gemini and Claude use "
        "their native APIs; open-weight models go through the Vertex MaaS endpoint.",
        visible=False,
    ),
    # OpenAI-compatible vendors, listed as first-class providers so users don't need to know the
    # "point the OpenAI slot at a different endpoint" trick (owner call, 2026-07-04). Each keeps
    # its own key profile; the endpoint is prefilled and editable (regional variants in `help`).
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
        endpoint_help="Prefilled with Alibaba Model Studio's international endpoint. China (Beijing): https://dashscope.aliyuncs.com/compatible-mode/v1",
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
        endpoint_help="Prefilled with the Meta Model API endpoint (public preview, US-only as of 2026-07).",
    ),
    # Resellers: many labs' models behind one key, using THEIR model namespaces (the curated
    # ids + display labels live in providers/matrix.py). TODO: add Groq here (+ its matrix
    # rows) once the current provider surface is tested — deliberately deferred to bound
    # how much needs verifying at once (owner call, 2026-07-04).
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
    ProviderDescriptor(
        name="ollama",
        title="Ollama (local preset)",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "OpenAI-compatible endpoint",
                secret=False,
                required=False,
                default=_normalize_ollama_url(DEFAULT_OLLAMA_URL),
                placeholder=_normalize_ollama_url(DEFAULT_OLLAMA_URL),
                help="Ollama's OpenAI-compatible endpoint. API key is optional for local use.",
            ),
            ProviderField("api_key", "API key (optional)", secret=True, required=False),
        ],
        build=_build_ollama,
        # Reliable native tool-calling + strong coding quality (verified). Pull with
        # `ollama pull qwen3-coder:30b`.
        recommended_model="qwen3-coder:30b",
        protocol=PROFILE_PROTOCOL_OPENAI,
        blurb="Preset: local Ollama through its OpenAI-compatible endpoint.",
    ),
]

_BY_NAME = {d.name: d for d in DESCRIPTORS}

_PROFILE_SECRET_PREFIX = "provider-profile:"
_LEGACY_PROTOCOL_MAP = {
    PROTOCOL_OPENAI_COMPAT: PROFILE_PROTOCOL_OPENAI,
    PROTOCOL_OPENAI_NATIVE: PROFILE_PROTOCOL_OPENAI,
    PROTOCOL_GEMINI: PROFILE_PROTOCOL_OPENAI,
    PROTOCOL_OLLAMA: PROFILE_PROTOCOL_OPENAI,
    PROTOCOL_ANTHROPIC: PROFILE_PROTOCOL_ANTHROPIC,
    PROTOCOL_BEDROCK: "legacy-bedrock",
    PROTOCOL_VERTEX: "legacy-vertex",
}


def provider_profile_key(name: str) -> str:
    """SecretStore location for an endpoint-bound provider profile."""
    return _PROFILE_SECRET_PREFIX + name


def profile_protocol(name: str, profile: dict[str, Any] | None = None) -> str:
    """Resolve a runtime protocol from profile metadata, never from a vendor brand."""
    configured = (profile or {}).get("protocol")
    if configured:
        return _LEGACY_PROTOCOL_MAP.get(str(configured), str(configured))
    descriptor = get_descriptor(name)
    return descriptor.protocol if descriptor else PROFILE_PROTOCOL_OPENAI


def migrate_legacy_provider_profiles(secrets: Any, prefs: dict[str, Any]) -> dict[str, Any]:
    """One-time migration from `provider:<vendor>` + `custom_providers`.

    Credentials move atomically into endpoint-bound `provider-profile:<name>` records and the
    legacy records are removed only after the new one exists.  The JSON prefs retain a compact,
    non-secret migration receipt so configuration is never silently discarded.
    """
    marker = prefs.get("provider_profile_migration")
    if isinstance(marker, dict) and marker.get("version") == 1:
        return marker
    custom = prefs.get("custom_providers") or {}
    profiles = dict(prefs.get("provider_profiles") or {})
    names = set(_BY_NAME) | set(custom)
    migrated: list[str] = []
    skipped: list[str] = []
    for name in sorted(names):
        legacy_key = f"provider:{name}"
        legacy = secrets.get(legacy_key)
        target_key = provider_profile_key(name)
        existing = secrets.get(target_key)
        meta = custom.get(name) if isinstance(custom, dict) else None
        raw_protocol = (meta or {}).get("protocol") if isinstance(meta, dict) else None
        descriptor = _BY_NAME.get(name)
        protocol = _LEGACY_PROTOCOL_MAP.get(
            str(raw_protocol or (descriptor.protocol if descriptor else PROFILE_PROTOCOL_OPENAI)),
            str(raw_protocol or (descriptor.protocol if descriptor else PROFILE_PROTOCOL_OPENAI)),
        )
        if legacy and not existing:
            target = dict(legacy)
            target["name"] = name
            target["protocol"] = protocol
            if protocol == PROFILE_PROTOCOL_OPENAI:
                target.setdefault("api_mode", "responses" if name == "openai" else "chat")
                if name == "gemini":
                    target.setdefault("base_url", DEFAULT_GEMINI_OPENAI_URL)
                elif name == "ollama":
                    target["base_url"] = _normalize_ollama_url(target.get("base_url"))
            elif protocol == PROFILE_PROTOCOL_ANTHROPIC:
                target.setdefault("base_url", DEFAULT_ANTHROPIC_URL)
            secrets.put(target_key, target)
            secrets.delete(legacy_key)
            migrated.append(name)
        elif legacy and existing:
            # Keep the already-migrated profile authoritative; remove only the duplicate legacy
            # record and leave an audit trail instead of silently merging secrets.
            secrets.delete(legacy_key)
            skipped.append(name)
        if name in custom:
            profiles[name] = {"protocol": protocol, "preset": False}
    if custom:
        prefs.pop("custom_providers", None)
    if profiles:
        prefs["provider_profiles"] = profiles
    receipt = {"version": 1, "migrated": migrated, "duplicates_removed": skipped}
    prefs["provider_profile_migration"] = receipt
    return receipt


def provider_descriptors() -> list[ProviderDescriptor]:
    return [d for d in [*DESCRIPTORS, *custom_provider_descriptors()] if d.visible]


def provider_names() -> list[str]:
    return [d.name for d in provider_descriptors()]


def custom_provider_names() -> list[str]:
    return list(CUSTOM_PROVIDERS.keys())


def is_custom_provider(name: str) -> bool:
    return name in CUSTOM_PROVIDERS


def get_descriptor(name: str) -> ProviderDescriptor | None:
    if name in _BY_NAME:
        return _BY_NAME[name]
    return _custom_descriptor(name)


def core_protocol_descriptors() -> dict[str, dict[str, Any]]:
    """The two user-selectable protocols for newly-created profiles."""
    return {
        PROFILE_PROTOCOL_OPENAI: {
            "title": "OpenAI",
            "needs_key": False,
            "fields": list(PROTOCOLS[PROTOCOL_OPENAI_COMPAT]["fields"]),
            "recommended_model": "gpt-4o",
        },
        PROFILE_PROTOCOL_ANTHROPIC: {
            "title": "Anthropic",
            "needs_key": True,
            "fields": [
                ProviderField("api_key", "Anthropic API key", secret=True, placeholder="sk-ant-…"),
                ProviderField("base_url", "Endpoint", required=False, default=DEFAULT_ANTHROPIC_URL),
            ],
            "recommended_model": "claude-fable-5",
        },
    }


def _custom_descriptor(name: str) -> ProviderDescriptor | None:
    meta = CUSTOM_PROVIDERS.get(name)
    if not meta:
        return None
    raw_protocol = meta["protocol"]
    canonical = _LEGACY_PROTOCOL_MAP.get(raw_protocol, raw_protocol)
    # The descriptor remains dynamic for the existing UI, while the stored profile routes by
    # canonical protocol. OpenAI aliases use the compatibility form (endpoint + optional key).
    proto = PROTOCOLS.get(raw_protocol) or PROTOCOLS[
        PROTOCOL_OPENAI_COMPAT if canonical == PROFILE_PROTOCOL_OPENAI else PROTOCOL_ANTHROPIC
    ]
    return ProviderDescriptor(
        name=name,
        # The alias is the provider's durable identity everywhere else: routing prefix,
        # prefs key, and SecretStore key. Keep the protocol as supporting metadata instead
        # of presenting two OpenAI-compatible aliases as the same provider.
        title=name,
        needs_key=proto["needs_key"],
        fields=list(proto["fields"]),
        build=proto["build"],
        recommended_model=proto["recommended_model"],
        env_key=proto.get("env_key"),
        blurb=proto["title"],
        protocol=canonical,
        preset=False,
    )


def custom_provider_descriptors() -> list[ProviderDescriptor]:
    out: list[ProviderDescriptor] = []
    for name, meta in CUSTOM_PROVIDERS.items():
        d = _custom_descriptor(name)
        if d:
            out.append(d)
    return out


def register_custom_provider(
    alias: str, protocol: str, fields_meta: dict[str, Any] | None = None
) -> None:
    """Register (or overwrite) a user-defined provider alias.

    `fields_meta` is opaque metadata kept alongside the alias (currently unused by the
    protocol builders but reserved for future per-alias UI state). Registration makes the
    alias route like any built-in so `alias:model` resolves and persists across restart
    once SessionManager re-hydrates it from prefs.
    """
    raw_protocol = protocol
    protocol = _LEGACY_PROTOCOL_MAP.get(protocol, protocol)
    if protocol not in (PROFILE_PROTOCOL_OPENAI, PROFILE_PROTOCOL_ANTHROPIC, "legacy-bedrock", "legacy-vertex"):
        raise ValueError(f"Unknown protocol: {protocol}")
    if not _valid_alias(alias):
        raise ValueError("Invalid provider alias.")
    # Keep old protocol spellings in the in-memory descriptor only for API compatibility;
    # persisted profiles are canonicalized by the manager/migration layer.
    CUSTOM_PROVIDERS[alias] = {"protocol": raw_protocol, **(fields_meta or {})}


def unregister_custom_provider(alias: str) -> None:
    CUSTOM_PROVIDERS.pop(alias, None)


def _valid_alias(alias: str) -> bool:
    return bool(alias and _ALIAS_RE.match(alias))


def build_provider_client(
    name: str, profile: dict[str, Any], secrets: Any
) -> ProviderClient:
    """Build a `ProviderClient` for `name` from its stored profile. Unknown → OpenAI default."""
    profile = profile or {}
    # Direct callers from extensions/tests that still pass the pre-profile shape keep their
    # legacy builder. SessionManager always writes `protocol`, so normal runtime routing is
    # protocol-first.
    if "protocol" not in profile:
        descriptor = get_descriptor(name) or _BY_NAME["openai"]
        return descriptor.build(profile, secrets)
    protocol = profile_protocol(name, profile)
    if protocol == PROFILE_PROTOCOL_OPENAI:
        return _build_openai(profile, secrets)
    if protocol == PROFILE_PROTOCOL_ANTHROPIC:
        return _build_anthropic(profile, secrets)
    # Legacy adapters stay available for existing configured profiles, but cannot become new
    # default protocol choices or leak into the normal OpenAI/Anthropic routing path.
    descriptor = get_descriptor(name) or _BY_NAME["openai"]
    return descriptor.build(profile, secrets)


def descriptor_configured(d: ProviderDescriptor, profile: dict[str, Any]) -> bool:
    """Whether a provider is usable with the given stored profile. Single-key providers:
    a stored or env key. Multi-field cloud providers (no `api_key` field, e.g. Bedrock):
    every required field present — their actual credentials may be ambient (~/.aws, ADC).
    """
    if not d.needs_key:
        return True  # keyless (Ollama) — usable out of the box
    profile = profile or {}
    if any(f.key == "api_key" for f in d.fields):
        return bool(profile.get("api_key")) or bool(
            d.env_key and os.environ.get(d.env_key)
        )
    return all(profile.get(f.key) for f in d.fields if f.required)


def detect_provider(api_key: str) -> str | None:
    """Best-effort provider guess from an API key's shape, for the onboarding auto-detect.
    Returns a known provider name or None. Mirrors the GUI's client-side detection so both agree.
    """
    key = (api_key or "").strip()
    if not key:
        return None
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith(("sk-", "sk_")):
        return "openai"
    return None


def _verify_bedrock(fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One cheap read-only Bedrock call (list models) with the same explicit → profile →
    ambient credential resolution the provider itself uses."""
    from providers.bedrock_provider import _session_kwargs

    def get(key: str) -> str | None:
        return (fields.get(key) or "").strip() or None

    try:
        import boto3
        import boto3.session  # noqa: F401 — submodule import so pyright resolves boto3.session
        from botocore.config import Config
    except ImportError:
        return {
            "ok": False,
            "error": "boto3 is not installed — `pip install 'delta[bedrock]'`.",
        }
    # Exactly one auth method is exercised — the one the form has selected. Per-method
    # required fields are checked here so the Test button says what's missing.
    method = get("auth_method") or "api_key"
    if method == "api_key" and not (
        get("bedrock_api_key") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    ):
        return {"ok": False, "error": "Enter a Bedrock API key to test."}
    if method == "iam" and not (
        get("aws_access_key_id") and get("aws_secret_access_key")
    ):
        return {"ok": False, "error": "Enter an access key ID and secret access key."}
    try:
        if method == "api_key":
            # The key rides the env var (boto3's only bearer channel); bearer then wins
            # over any ambient SigV4 credentials for Bedrock calls.
            if get("bedrock_api_key"):
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = get("bedrock_api_key")
            session_kwargs: dict[str, Any] = {}
        elif method == "profile":
            # Blank profile → the default credential chain (env vars / ~/.aws / role).
            session_kwargs = _session_kwargs(get("aws_profile"), None, None, None)
        else:  # iam
            session_kwargs = _session_kwargs(
                None,
                get("aws_access_key_id"),
                get("aws_secret_access_key"),
                get("aws_session_token"),
            )
        session = boto3.session.Session(**session_kwargs)
        client = session.client(
            "bedrock",
            region_name=get("region"),
            config=Config(connect_timeout=timeout, read_timeout=timeout),
        )
        client.list_foundation_models()
    except Exception as exc:
        kind = exc.__class__.__name__
        if kind == "NoCredentialsError":
            return {
                "ok": False,
                "error": "No AWS credentials found — enter keys or a profile, or run "
                "`aws configure` / `aws sso login` first.",
            }
        if kind == "ProfileNotFound":
            return {"ok": False, "error": f"{exc}"}
        if kind == "ClientError":
            code = (getattr(exc, "response", {}) or {}).get("Error", {}).get("Code", "")
            if code in ("UnrecognizedClientException", "InvalidSignatureException"):
                return {"ok": False, "error": "AWS rejected the credentials."}
            if code in ("AccessDeniedException", "AccessDenied"):
                return {
                    "ok": False,
                    "error": "Credentials work but lack Bedrock access (bedrock:ListFoundationModels).",
                }
            return {"ok": False, "error": f"AWS Bedrock returned {code or kind}."}
        return {"ok": False, "error": f"Couldn't reach AWS Bedrock ({kind})."}
    return {"ok": True}


# Verify probe: countTokens on a stable Gemini model — free (no generation), works with
# plain ADC (the model list/GET endpoints 403/404 under user credentials — checked live
# 2026-07-26), and exercises project + location + API enablement in one call.
_VERTEX_PROBE_MODEL = "gemini-2.5-flash"
_VERTEX_PROBE_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _verify_vertex(fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One cheap call (countTokens) through the SELECTED auth method: ADC /
    service-account bearer, or the express API key header."""
    import httpx

    from providers.vertex_provider import load_credentials

    project = (fields.get("project") or "").strip()
    location = (fields.get("location") or "").strip()
    method = (fields.get("auth_method") or "").strip() or (
        "service_account" if (fields.get("service_account_json") or "").strip() else "adc"
    )
    if method == "api_key":
        key = (fields.get("vertex_api_key") or "").strip()
        if not key:
            return {"ok": False, "error": "Enter a Vertex API key to test."}
        try:
            # Express mode is global — no region host, no project in the path.
            resp = httpx.post(
                "https://aiplatform.googleapis.com/v1/publishers/google/models/"
                f"{_VERTEX_PROBE_MODEL}:countTokens",
                headers={"x-goog-api-key": key},
                json=_VERTEX_PROBE_BODY,
                timeout=timeout,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Couldn't reach Vertex AI ({exc.__class__.__name__}).",
            }
        if resp.status_code < 300:
            return {"ok": True}
        if resp.status_code in (401, 403):
            return {"ok": False, "error": "Google rejected the API key."}
        return {"ok": False, "error": f"Vertex AI returned HTTP {resp.status_code}."}
    if method == "service_account" and not (fields.get("service_account_json") or "").strip():
        return {"ok": False, "error": "Paste a service-account JSON to test."}
    try:
        creds = None
        if method == "service_account":
            creds = load_credentials(fields.get("service_account_json"))
        if creds is None:
            import google.auth

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        from google.auth.transport.requests import Request

        creds.refresh(Request())
    except Exception as exc:
        kind = exc.__class__.__name__
        if kind == "DefaultCredentialsError":
            return {
                "ok": False,
                "error": "No Google Cloud credentials found — paste a service-account "
                "JSON, or run `gcloud auth application-default login` first.",
            }
        if kind in ("RefreshError", "MalformedError", "JSONDecodeError", "ValueError"):
            return {"ok": False, "error": "Google rejected the credentials."}
        return {"ok": False, "error": f"Couldn't load Google credentials ({kind})."}
    from providers.vertex_provider import _regional_host

    try:
        resp = httpx.post(
            f"https://{_regional_host(location)}/v1/projects/{project}"
            f"/locations/{location}/publishers/google/models/"
            f"{_VERTEX_PROBE_MODEL}:countTokens",
            headers={"Authorization": f"Bearer {creds.token}"},
            json=_VERTEX_PROBE_BODY,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't reach Vertex AI ({exc.__class__.__name__})."}
    if resp.status_code < 300:
        return {"ok": True}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": "Credentials work but lack Vertex AI access in this project.",
        }
    if resp.status_code == 404:
        return {"ok": False, "error": "Project or location not found on Vertex AI."}
    return {"ok": False, "error": f"Vertex AI returned HTTP {resp.status_code}."}


def verify_provider_key(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    fields: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Validate a provider's credentials with one cheap, read-only call (list models) — the same
    pattern connectors use to validate tokens. Transient: callers pass the key directly so a user
    can Test before saving. Never raises; returns {ok, error?}. Multi-field cloud providers
    (Bedrock, Vertex) take their whole form via `fields`; everyone else uses api_key/base_url.
    """
    import httpx

    d = get_descriptor(name) or _BY_NAME["openai"]
    fields = fields or {}
    key = (api_key or "").strip()
    protocol = profile_protocol(name, fields)
    if name == "bedrock":
        return _verify_bedrock(fields or {}, timeout)
    if name == "vertex":
        return _verify_vertex(fields or {}, timeout)
    try:
        if name == "gemini" and "protocol" not in fields:
            # Compatibility for callers of the legacy helper. The profile-aware manager passes
            # protocol=openai and therefore uses the preset's OpenAI-compatible endpoint.
            resp = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": key},
                timeout=timeout,
            )
        elif protocol == PROFILE_PROTOCOL_ANTHROPIC:
            base = ((base_url or "").strip().rstrip("/") or DEFAULT_ANTHROPIC_URL)
            resp = httpx.get(
                base + "/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                timeout=timeout,
            )
        elif name == "ollama":
            base = _normalize_ollama_url(base_url)
            resp = httpx.get(base.rstrip("/") + "/models", timeout=timeout)
        elif is_custom_provider(name):
            # Custom alias — build the request as the endpoint its protocol dictates.
            resp = _fetch_models_request(name, key, base_url, timeout)
        else:  # openai + any OpenAI-compatible endpoint (Azure, OpenRouter, vendors, vLLM…)
            default_base = next(
                (f.default for f in d.fields if f.key == "base_url" and f.default), ""
            )
            base = (
                (base_url or "").strip().rstrip("/")
                or default_base.rstrip("/")
                or (DEFAULT_GEMINI_OPENAI_URL.rstrip("/") if name == "gemini" else DEFAULT_OPENAI_URL)
            )
            # Same keyless rule: omit the header rather than send a bare "Bearer ".
            resp = httpx.get(
                base + "/models",
                headers={"Authorization": f"Bearer {key}"} if key else {},
                timeout=timeout,
            )
    except Exception as exc:  # DNS/connection/timeout — never let it bubble to a 500
        return {
            "ok": False,
            "error": f"Couldn't reach {d.title} ({exc.__class__.__name__}).",
        }

    if resp.status_code < 300:
        return {"ok": True}
    if resp.status_code in (401, 403):
        if name == "ollama":
            return {"ok": False, "error": "Server rejected the request."}
        return {"ok": False, "error": "Invalid API key."}
    if resp.status_code == 404 and name == "ollama":
        return {
            "ok": False,
            "error": "Reached the server, but no OpenAI-compatible /v1 API there.",
        }
    if resp.status_code == 404:
        return {"ok": False, "error": "Model listing is not supported by this endpoint — add a model ID manually."}
    return {"ok": False, "error": f"{d.title} returned HTTP {resp.status_code}."}


def _fetch_models_request(
    name: str, api_key: str | None, base_url: str | None, timeout: float = 10.0
) -> Any:
    """Build the read-only models-list request for a custom provider, honouring its protocol.

    Used by both `verify_provider_key` and `fetch_provider_models` so a Test and a Fetch stay
    wired identically. Raises nothing on network — callers translate exceptions into
    {ok/error}. Bedrock/Vertex unsupported for fetching return a synthetic response the
    caller will translate into a clear error.
    """
    import httpx

    meta = CUSTOM_PROVIDERS.get(name)
    if not meta:
        raise RuntimeError(f"Unknown provider: {name}")
    protocol = profile_protocol(name, meta)
    key = (api_key or "").strip()

    if meta.get("protocol") == PROTOCOL_OLLAMA:
        base = _normalize_ollama_url(base_url)
        return httpx.get(base.rstrip("/") + "/models", timeout=timeout)

    if protocol == PROFILE_PROTOCOL_OPENAI:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("No server address configured.")
        # No auth header at all when keyless — httpx rejects a bare "Bearer " value
        # (LocalProtocolError), which used to make every keyless Test/Fetch fail even
        # against a perfectly reachable local server.
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return httpx.get(
            base + "/models",
            headers=headers,
            timeout=timeout,
        )

    if protocol == PROFILE_PROTOCOL_ANTHROPIC:
        base = (base_url or "").strip().rstrip("/") or DEFAULT_ANTHROPIC_URL
        return httpx.get(
            base + "/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=timeout,
        )

    if protocol == PROTOCOL_OLLAMA:
        base = _normalize_ollama_url(base_url)
        return httpx.get(base.rstrip("/") + "/models", timeout=timeout)

    return _unsupported_fetch(protocol)


def _unsupported_fetch(protocol: str) -> Any:
    # Bedrock/Vertex have no open model list to enumerate; a synthetic response lets the
    # shared caller translate this into the protocol-named error rather than a crash.
    return SimpleNamespace(
        status_code=0, json=lambda: {"data": []}, request=SimpleNamespace(url="")
    )


def fetch_provider_models(
    name: str, profile: dict[str, Any], secrets: Any, timeout: float = 10.0
) -> dict[str, Any]:
    """Fetch the model id list for a provider alias. Read-only; returns {ok, models?}.

    Reuses the same probe request shape as `verify_provider_key` so a passing Test implies a
    passing Fetch. OpenAI-compatible/OpenAI/Anthropic/Gemini/Ollama return bare ids from the
    `/models` response. Bedrock/Vertex are intentionally unsupported for fetching (their
    model namespaces are curated server-side) and return a clear error.
    """
    title = get_descriptor(name).title if get_descriptor(name) else name
    meta = CUSTOM_PROVIDERS.get(name) or {}
    protocol = profile_protocol(name, profile)
    if protocol in ("legacy-bedrock", "legacy-vertex"):
        return {
            "ok": False,
            "error": f"{title} doesn't expose a model list to fetch — add its models manually.",
        }
    api_key = (profile.get("api_key") or "").strip()
    base_url = (profile.get("base_url") or "").strip() or None
    try:
        if is_custom_provider(name):
            resp = _fetch_models_request(name, api_key or None, base_url, timeout)
        else:
            import httpx

            if protocol == PROFILE_PROTOCOL_ANTHROPIC:
                base = (base_url or DEFAULT_ANTHROPIC_URL).rstrip("/")
                resp = httpx.get(
                    base + "/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    timeout=timeout,
                )
            else:
                if name == "ollama":
                    base = _normalize_ollama_url(base_url)
                elif name == "gemini":
                    base = (base_url or DEFAULT_GEMINI_OPENAI_URL).rstrip("/")
                else:
                    base = (base_url or DEFAULT_OPENAI_URL).rstrip("/")
                resp = httpx.get(
                    base + "/models",
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                    timeout=timeout,
                )
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't reach {title} ({exc.__class__.__name__})."}
    if resp.status_code < 300:
        data = resp.json()
        ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
        return {"ok": True, "models": ids}
    if resp.status_code in (401, 403):
        return {"ok": False, "error": "Invalid API key."}
    if resp.status_code == 404:
        return {"ok": False, "error": "Model listing is not supported by this endpoint — add a model ID manually."}
    return {
        "ok": False,
        "error": f"{title} returned HTTP {resp.status_code}.",
    }
