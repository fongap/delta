"""Provider registry, model picker state, and persisted app settings/prefs.

Extracted verbatim from SessionManager (see manager.py); composed back via
mixin inheritance so behavior is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from integrations.connectors import experimental_enabled

from providers import (
    descriptor_configured,
    get_descriptor,
    is_custom_provider,
    provider_descriptors,
    provider_profile_key,
    profile_protocol,
    register_custom_provider,
    unregister_custom_provider,
    verify_provider_key,
)


from services.server.manager_contract import ManagerHostState


class ProvidersSettingsMixin(ManagerHostState):

    # -- model providers ---------------------------------------------------------
    def get_providers(self) -> list[dict[str, Any]]:
        """Descriptor + per-provider status for the Settings UI. Never returns secret values;
        Non-secret field values (for example an endpoint) are returned so the form can prefill.
        """
        out: list[dict[str, Any]] = []
        for d in provider_descriptors():
            profile = self.secrets.get(provider_profile_key(d.name)) or {}
            configured = descriptor_configured(d, profile)
            values = {
                f.key: profile.get(f.key)
                for f in d.fields
                if not f.secret and profile.get(f.key)
            }
            custom = is_custom_provider(d.name)
            custom_meta = (self._prefs.get("provider_profiles") or {}).get(d.name, {})
            out.append(
                {
                    **d.to_dict(),
                    "configured": configured,
                    "values": values,
                    "suggested_models": self._suggested_models(d.name),
                    # Key hygiene for the Settings pane: when the key was saved (date, stamped
                    # by set_provider) and when the provider last served a completion (epoch,
                    # stamped by the router's on_use hook). Absent for env-only config.
                    "key_set_at": profile.get("key_set_at"),
                    "last_used_at": (self._prefs.get("provider_last_used") or {}).get(
                        d.name
                    ),
                    # Custom-config-first markers: the GUI uses these to tell a user-defined
                    # alias apart from a built-in provider (edit/delete vs remove-key) and to
                    # preselect the protocol dropdown when editing. `alias` is the name itself
                    # for custom providers (kept distinct so the field is self-describing).
                    "custom": custom,
                    "protocol": custom_meta.get("protocol") if custom else None,
                    "alias": d.name if custom else None,
                }
            )
        return out


    def _note_provider_use(self, name: str) -> None:
        """Router on_use hook: remember when a provider last served a completion. Persisted
        THROTTLED (once per provider per minute) — this fires on every model call, from engine
        threads, and prefs.json isn't a place for a write-per-token-of-work."""
        import time

        now = time.time()
        used = self._prefs.setdefault("provider_last_used", {})
        if now - float(used.get(name) or 0) < 60:
            return
        used[name] = now
        try:
            self._save_prefs()
        except OSError:
            pass


    # Suggestions for the OpenAI-compatible vendor providers (checked against vendor docs
    # 2026-07-04; refresh alongside `recommended_model` in providers/registry.py).
    COMPAT_MODELS = {
        "zai": ["glm-5.2", "glm-4.6"],
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "kimi": ["kimi-k2.6", "kimi-k2.5"],
        "minimax": ["MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M3"],
        "qwen": ["qwen3-max", "qwen3-coder-plus", "qwen-plus"],
        "xai": ["grok-4.3", "grok-4"],
        "mistral": ["mistral-large-latest", "mistral-small-latest"],
    }

    def _suggested_models(self, name: str) -> list[str]:
        """Bare model-name suggestions for the 'add model' form (datalist), per provider.
        Uses the curated matrix plus compatible-vendor extras the matrix doesn't vouch for."""
        from providers.matrix import models_for_provider

        return list(
            dict.fromkeys(
                [*models_for_provider(name), *self.COMPAT_MODELS.get(name, [])]
            )
        )


    def set_provider(
        self,
        name: str,
        fields: dict[str, Any] | None,
        require_complete: bool = True,
    ) -> dict[str, Any]:
        """Store a provider's config in its `provider:<name>` SecretStore profile and rebuild
        its cached client. Merges provided fields into any existing profile.

        `require_complete=False` (used when first creating a custom provider) stores whatever
        fields were given without rejecting a provider that isn't fully configured yet — the
        alias is first-class and the key/endpoint can be filled in later.
        """
        d = get_descriptor(name)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        fields = fields or {}
        profile = dict(self.secrets.get(provider_profile_key(name)) or {})
        profile.setdefault("name", name)
        profile.setdefault("protocol", d.protocol)
        if d.protocol == "openai":
            profile.setdefault("api_mode", "responses" if name == "openai" else "chat")
        for f in d.fields:
            if f.key not in profile and f.default:
                profile[f.key] = f.default
            if f.key not in fields:
                continue
            val = fields.get(f.key)
            if isinstance(val, str):
                val = val.strip()
            if val:
                profile[f.key] = val
            elif not f.required:
                profile.pop(f.key, None)
        if require_complete:
            missing = [f.label for f in d.fields if f.required and not profile.get(f.key)]
            if missing:
                return {"ok": False, "error": "missing: " + ", ".join(missing)}
        # A (re)pasted key stamps its save date — Settings shows "key added <date>" so stale
        # keys are visible. Endpoint-only saves keep the original stamp.
        if isinstance(fields.get("api_key"), str) and fields["api_key"].strip():
            from datetime import date

            profile["key_set_at"] = date.today().isoformat()
        self.secrets.put(provider_profile_key(name), profile)
        self._refresh_provider(name)
        # Convenience: if the provider recommends a model and it's actually available, add it to
        # the curated list so it shows up in the composer right after configuring the provider.
        rec = d.recommended_model
        added: str | None = None
        if rec and rec in self._suggested_models(name):
            # OpenAI models stay bare (the router's default); others carry their prefix.
            added = rec if name == "openai" else f"{name}:{rec}"
            self.add_model(added)
        # First working provider wins the default: on a fresh install there is no preset
        # model default (Delta ships without a vendor/model preseed), so the first provider
        # with a key and an available recommended model becomes the default. A default that
        # already works is never stolen.
        if added and not self._provider_configured(self._model_provider(self.model)):
            self.set_default_model(added)
        return {"ok": True, "provider": name, "recommended_model": rec}


    def remove_provider(self, name: str) -> dict[str, Any]:
        """Forget a provider's stored config (Settings ▸ Models "Remove key"). The whole
        `provider:<name>` profile goes — key, endpoint, key_set_at — so the provider reads
        as never configured. Curated models stay; they just gray out until a new key."""
        d = get_descriptor(name)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        self.secrets.delete(provider_profile_key(name))
        self._refresh_provider(name)
        return {"ok": True, "provider": name}


    def is_custom_registered(self, alias: str) -> bool:
        """True when `alias` is a user-registered custom provider (not a built-in)."""
        return is_custom_provider(alias)


    def create_custom_provider(
        self, alias: str, protocol: str, fields: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Register a user-defined provider alias (custom-config-first model setup).

        Persists `alias -> protocol` to prefs (so routing survives a restart), injects the
        dynamic descriptor into the registry, then reuses `set_provider` to store the
        credential/config profile in the SecretStore and rebuild the cached client.
        """
        alias = (alias or "").strip()
        # An alias that already resolves to a descriptor (built-in or an existing custom
        # provider) must not be silently shadowed — reject it for a clear error.
        if get_descriptor(alias) is not None and not is_custom_provider(alias):
            return {"ok": False, "error": f"provider already exists: {alias}"}
        # Validate alias + protocol up front (register_custom_provider raises ValueError).
        try:
            register_custom_provider(alias, protocol)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        # Persist the registration so __init__ re-hydrates it after a restart.
        descriptor = get_descriptor(alias)
        canonical = descriptor.protocol if descriptor is not None else protocol
        custom = dict(self._prefs.get("provider_profiles") or {})
        custom[alias] = {"protocol": canonical, "preset": False}
        self._prefs["provider_profiles"] = custom
        self._save_prefs()
        # Store the actual config (key/endpoint) under provider:<alias> and rebuild the client.
        # A custom provider is first-class the moment it's named — its key/endpoint can be
        # filled in later, so don't reject a not-yet-complete profile here.
        result = self.set_provider(alias, fields, require_complete=False)
        if not result.get("ok"):
            # Roll back the registration if set_provider rejected the fields so we don't
            # leave a half-registered provider behind.
            self.remove_custom_provider(alias)
            return result
        return {"ok": True, "provider": alias, "protocol": protocol,
                "recommended_model": result.get("recommended_model")}


    def remove_custom_provider(self, alias: str) -> dict[str, Any]:
        """Delete a custom provider alias: unregister it, drop its SecretStore profile,
        and drop the prefs entry so it doesn't come back on restart. Its `alias:*` models
        leave the picker too — with the alias gone they'd otherwise route as OpenAI."""
        if not is_custom_provider(alias):
            return {"ok": False, "error": f"unknown provider: {alias}"}
        unregister_custom_provider(alias)
        self.secrets.delete(provider_profile_key(alias))
        custom = dict(self._prefs.get("provider_profiles") or {})
        custom.pop(alias, None)
        self._prefs["provider_profiles"] = custom
        # Drop the alias's own models (and any hide-marks for them) so nothing routes
        # through a provider that no longer exists.
        prefix = alias + ":"
        models = self._prefs.get("models") or []
        self._prefs["models"] = [m for m in models if not m.startswith(prefix)]
        hidden = self._prefs.get("hidden_models") or []
        self._prefs["hidden_models"] = [m for m in hidden if not m.startswith(prefix)]
        if not self._prefs["hidden_models"]:
            self._prefs.pop("hidden_models", None)
        self._save_prefs()
        self._refresh_provider(alias)
        return {"ok": True, "provider": alias}


    def verify_provider(
        self, name: str, fields: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Test a provider's credentials with a live read-only call, WITHOUT persisting them, so
        onboarding can offer a "Test" button. Falls back to stored/env values when the form left
        a field blank (e.g. testing an already-configured provider)."""
        import os

        d = get_descriptor(name)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        fields = fields or {}
        profile = self.secrets.get(provider_profile_key(name)) or {}
        merged = {}
        for f in d.fields:
            val = fields.get(f.key) or profile.get(f.key) or ""
            if isinstance(val, str):
                val = val.strip()
            if val:
                merged[f.key] = val
        merged["protocol"] = profile_protocol(name, profile)
        api_key = merged.get("api_key", "")
        base_url = str(merged.get("base_url") or "").strip().rstrip("/")
        # Environment keys belong only to the official first-party endpoints. A custom profile
        # must supply its own key; otherwise a shell's OpenAI/Anthropic credential could be sent
        # to an unrelated URL during Test or model discovery.
        allow_env = (
            (name == "openai" and base_url in ("", "https://api.openai.com/v1"))
            or (name == "anthropic" and base_url in ("", "https://api.anthropic.com"))
        )
        if not api_key and allow_env and d.env_key:
            api_key = os.environ.get(d.env_key, "").strip()
        has_key_field = any(f.key == "api_key" for f in d.fields)
        if d.needs_key and has_key_field and not api_key:
            return {"ok": False, "error": "Enter an API key to test."}
        if d.needs_key and not has_key_field:
            missing = [f.label for f in d.fields if f.required and not merged.get(f.key)]
            if missing:
                return {"ok": False, "error": "missing: " + ", ".join(missing)}
        return verify_provider_key(
            name, api_key=api_key, base_url=base_url, fields=merged
        )


    def _model_provider(self, model: str) -> str:
        """The provider a model string routes to (known `prefix:` or the OpenAI default)."""
        if ":" in (model or ""):
            prefix = model.split(":", 1)[0]
            if get_descriptor(prefix) is not None:
                return prefix
        return "openai"


    def _provider_configured(self, name: str) -> bool:
        d = get_descriptor(name)
        if d is None:
            return False
        return descriptor_configured(d, self.secrets.get(provider_profile_key(name)) or {})


    # -- settings / prefs (model API key, default model, onboarding) -------------
    def _prefs_path(self) -> Path:
        return self._data_base / "prefs.json"


    def _load_prefs(self) -> dict[str, Any]:
        try:
            return json.loads(self._prefs_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


    def _save_prefs(self) -> None:
        self._prefs_path().write_text(
            json.dumps(self._prefs, indent=2), encoding="utf-8"
        )


    # -- direct-message routing -------------------------------------------------
    def dm_session(self) -> str | None:
        """The session a DM to the bot is routed to (user-designated). None → DMs are parked."""
        sid = self._prefs.get("dm_session")
        return sid or None


    def set_dm_session(self, session_id: str | None) -> dict[str, Any]:
        """Designate (or clear, with a falsy id) the session that handles incoming DMs."""
        sid = (session_id or "").strip()
        if sid:
            self._prefs["dm_session"] = sid
        else:
            self._prefs.pop("dm_session", None)
        self._save_prefs()
        return {"ok": True, "dm_session": self.dm_session()}


    def _curated_models(self) -> list[str]:
        """The models offered in the composer's selector: every curated-matrix model
        (`get_settings` culls the ones whose provider has no key) plus custom ids the user
        added, minus matrix models they removed. Deliberately NO built-in seed list — a
        fresh install offers nothing until a provider key exists, and then exactly that
        provider's matrix models appear. The active default is always kept selectable.
        """
        from providers.matrix import MATRIX

        user = self._prefs.get("models")
        user = user if isinstance(user, list) else []
        hidden = set(self._prefs.get("hidden_models") or [])
        models = [m for m in [*MATRIX, *user] if m not in hidden]
        # Drop falsy ids — an unset default (`self.model == ""`) must never surface as a
        # blank selectable entry at the top of the picker.
        return list(dict.fromkeys(m for m in [self.model, *models] if m))


    def add_model(self, model: str) -> dict[str, Any]:
        """Add a model id (for example `gpt-4o` or `my-gateway:model-id`) to the picker.
        Custom ids persist in prefs; a previously removed matrix model is just unhidden
        (storing it too would shadow future matrix updates)."""
        from providers.matrix import MATRIX

        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "empty model"}
        hidden = [m for m in self._prefs.get("hidden_models") or [] if m != model]
        if hidden:
            self._prefs["hidden_models"] = hidden
        else:
            self._prefs.pop("hidden_models", None)
        models = self._prefs.get("models")
        models = models if isinstance(models, list) else []
        if model not in models and model not in MATRIX:
            models.append(model)
        self._prefs["models"] = models
        self._save_prefs()
        return {"ok": True, **self.get_settings()}


    def remove_model(self, model: str) -> dict[str, Any]:
        """Remove a model id from the picker. Custom ids are dropped; matrix models are
        hidden by id (the matrix is derived, not stored, so a bare drop would resurrect
        them on the next read). The default model can never be hidden — the picker's
        invariant is default ∈ enabled (the UI asks for a new default first)."""
        from providers.matrix import MATRIX

        model = (model or "").strip()
        if model and model == self._prefs.get("default_model"):
            return {"ok": False, "error": "default model cannot be hidden"}
        models = self._prefs.get("models")
        models = models if isinstance(models, list) else []
        self._prefs["models"] = [m for m in models if m != model]
        if model in MATRIX:
            hidden = self._prefs.get("hidden_models") or []
            if model not in hidden:
                self._prefs["hidden_models"] = [*hidden, model]
        self._save_prefs()
        return {"ok": True, **self.get_settings()}


    def get_settings(self) -> dict[str, Any]:
        """Model-access + UI status. Never returns the key; `source` says where it comes from."""
        import os

        env_key = bool(os.environ.get("OPENAI_API_KEY"))
        stored = bool((self.secrets.get(provider_profile_key("openai")) or {}).get("api_key"))
        # Only surface models whose provider is actually configured — the composer picker
        # reflects exactly what's connected. The active default is always kept selectable
        # (it's hidden behind the "No model" state until a provider is connected anyway).
        def _selectable(m: str) -> bool:
            provider = self._model_provider(m)
            return self._provider_configured(provider)

        selectable = [m for m in self._curated_models() if _selectable(m)]
        # Keep the active default selectable even if culled above — but never insert a
        # blank entry when no default has been chosen yet.
        if self.model and self.model not in selectable:
            selectable.insert(0, self.model)
        from providers.matrix import model_context_windows, model_labels

        return {
            "provider": "openai",
            "model": self.model,
            "models": selectable,
            # Curated-matrix display names ({full id → "GLM-5.2 · via Together"}) so every
            # picker shows human labels; custom models absent here render their raw id.
            "model_labels": model_labels(),
            # {full id → context window in tokens}, verified matrix entries only —
            # drives the composer's context-fill meter (absent id → meter hides).
            "model_context_windows": model_context_windows(),
            "has_key": env_key or stored,
            # Provider-agnostic "can this default model actually run?" — true when the default
            # model's provider is configured (any provider, not just OpenAI). Drives the GUI's
            # "No model connected" composer chip and the onboarding Skip warning.
            "model_ready": self._provider_configured(self._model_provider(self.model)),
            "source": "env" if env_key else ("store" if stored else None),
            "onboarded": bool(self._prefs.get("onboarded")),
            # Single source of truth for the user's language. Drives the GUI's i18n and the
            # agent/persona/compaction natural-language behavior (§3). Absent → null: the
            # GUI falls back to its own default rather than the server guessing.
            "language": self._prefs.get("language") or None,
            "experimental_connectors": experimental_enabled(self.secrets),
            "surfaces": self._surfaces(),
            "nav_layout": self._nav_layout(),
            "sessions_peek": self.sessions_peek(),
            "context_bar": self.context_bar(),
            "scratch_base": self._prefs.get("scratch_base")
            or self.DEFAULT_SCRATCH_BASE,
            # Real on-disk secrets location, so the UI shows the OS-native path instead of a
            # hardcoded POSIX one (Windows -> %APPDATA%\delta, macOS/Linux -> ~/.config).
            "secrets_path": str(self.secrets.path),
            **self.pdf_settings(),
            **self.compaction_settings_payload(),
        }


    def _surfaces(self) -> dict[str, bool]:
        """Which session surfaces are shown in the sidebar. Cowork is always on; Chat and Code
        are opt-in (default off) so a new user sees Cowork only."""
        return {
            "cowork": True,
            "chat": bool(self._prefs.get("show_chat", False)),
            "code": bool(self._prefs.get("show_code", False)),
        }


    def set_surfaces(
        self, chat: bool | None = None, code: bool | None = None
    ) -> dict[str, Any]:
        """Toggle Chat/Code visibility (Cowork is always shown). Persisted in prefs."""
        if chat is not None:
            self._prefs["show_chat"] = bool(chat)
        if code is not None:
            self._prefs["show_code"] = bool(code)
        self._save_prefs()
        return {"ok": True, "surfaces": self._surfaces()}


    def _nav_layout(self) -> str:
        """Sidebar layout: ``"flat"`` (default) or ``"grouped"`` (by persona). Persisted in
        prefs (UI-REFRESH §7)."""
        return "grouped" if self._prefs.get("nav_layout") == "grouped" else "flat"


    def set_nav_layout(self, nav_layout: str) -> dict[str, Any]:
        """Set + persist the sidebar layout. Unknown values fall back to ``"flat"``."""
        value = "grouped" if (nav_layout or "").strip() == "grouped" else "flat"
        self._prefs["nav_layout"] = value
        self._save_prefs()
        return {"ok": True, "nav_layout": value}


    DEFAULT_SESSIONS_PEEK = 5

    def sessions_peek(self) -> int:
        """How many sessions a sidebar group shows before "Show more" (owner ask, 2026-07-03)."""
        try:
            n = int(self._prefs.get("sessions_peek", self.DEFAULT_SESSIONS_PEEK))
        except (TypeError, ValueError):
            n = self.DEFAULT_SESSIONS_PEEK
        return max(1, min(n, 50))


    def set_sessions_peek(self, n: int) -> dict[str, Any]:
        try:
            self._prefs["sessions_peek"] = max(1, min(int(n), 50))
        except (TypeError, ValueError):
            return {"ok": False, "error": "sessions_peek must be a number"}
        self._save_prefs()
        return {"ok": True, "sessions_peek": self.sessions_peek()}


    def context_bar(self) -> bool:
        """Whether the composer shows the context-window fill bar. OFF by default (owner
        ask): the chip then states the session total, and the popover keeps both numbers."""
        return bool(self._prefs.get("context_bar", False))


    def set_context_bar(self, shown: Any) -> dict[str, Any]:
        self._prefs["context_bar"] = bool(shown)
        self._save_prefs()
        return {"ok": True, "context_bar": self.context_bar()}


    # -- PDF attachments / token savings (owner ask, 2026-07-17) ----------------
    DEFAULT_PDF_MAX_PAGES = 20
    DEFAULT_PDF_MAX_MB = 10

    def pdf_settings(self) -> dict[str, Any]:
        """Fallback mode for models without native PDF support + the attach-time
        thresholds (Settings → Token savings: big PDFs quietly eat tokens)."""
        from core.pdf_support import FALLBACK_MODES

        mode = self._prefs.get("pdf_fallback")
        try:
            pages = int(self._prefs.get("pdf_max_pages", self.DEFAULT_PDF_MAX_PAGES))
        except (TypeError, ValueError):
            pages = self.DEFAULT_PDF_MAX_PAGES
        try:
            mb = int(self._prefs.get("pdf_max_mb", self.DEFAULT_PDF_MAX_MB))
        except (TypeError, ValueError):
            mb = self.DEFAULT_PDF_MAX_MB
        return {
            "pdf_fallback": mode if mode in FALLBACK_MODES else "text",
            "pdf_max_pages": max(1, min(pages, 100)),
            "pdf_max_mb": max(1, min(mb, 10)),
        }


    def compaction_settings(self) -> dict[str, Any]:
        """The live auto-compaction knobs (OPE-27) — read by every engine per check, so a
        Settings change applies without a rebuild. Only the two spec'd overrides plus the
        summarizer-model pin; absent keys fall back to compaction.py defaults."""
        from core.compaction import DEFAULT_CAP_TOKENS, DEFAULT_THRESHOLD_PCT

        return {
            "threshold_pct": float(
                self._prefs.get("compaction_threshold_pct") or DEFAULT_THRESHOLD_PCT
            ),
            "cap_tokens": int(
                self._prefs.get("compaction_cap_tokens") or DEFAULT_CAP_TOKENS
            ),
            # "" → the session's own model (engine falls back to self.model).
            "model": str(self._prefs.get("compaction_model") or ""),
            # Weighted prefill accounting (Codex-absorbed): < 1.0 reflects that input
            # tokens cost less than output tokens on shared gateways. 1.0 = classic.
            "prefill_weight": float(self._prefs.get("compaction_prefill_weight") or 1.0),
        }


    def compaction_settings_payload(self) -> dict[str, Any]:
        """The same knobs under REST-facing names (prefixed to keep /v1/settings flat)."""
        settings = self.compaction_settings()
        return {
            "compaction_threshold_pct": settings["threshold_pct"],
            "compaction_cap_tokens": settings["cap_tokens"],
            "compaction_model": settings["model"],
            "compaction_prefill_weight": settings["prefill_weight"],
        }


    def set_compaction_settings(
        self,
        threshold_pct: Any = None,
        cap_tokens: Any = None,
        model: Any = None,
        prefill_weight: Any = None,
    ) -> dict[str, Any]:
        """Persist the auto-compaction overrides (OPE-27). Threshold is a percentage of
        the model's context window (10–95); the cap is an absolute token ceiling; model
        pins the summarizer ('' → the session's own model). Engines read these live via
        `compaction_settings()`, so changes apply to running sessions immediately."""
        if threshold_pct is not None:
            try:
                pct = float(threshold_pct)
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_threshold_pct must be a number"}
            if not 0.10 <= pct <= 0.95:
                return {
                    "ok": False,
                    "error": "compaction_threshold_pct must be between 0.10 and 0.95",
                }
            self._prefs["compaction_threshold_pct"] = pct
        if cap_tokens is not None:
            try:
                self._prefs["compaction_cap_tokens"] = max(
                    10_000, min(int(cap_tokens), 2_000_000)
                )
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_cap_tokens must be a number"}
        if model is not None:
            self._prefs["compaction_model"] = str(model)
        if prefill_weight is not None:
            try:
                weight = float(prefill_weight)
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_prefill_weight must be a number"}
            if not 0.01 <= weight <= 1.0:
                return {
                    "ok": False,
                    "error": "compaction_prefill_weight must be between 0.01 and 1.0",
                }
            self._prefs["compaction_prefill_weight"] = weight
        self._save_prefs()
        return {"ok": True, **self.compaction_settings()}


    def set_pdf_settings(
        self,
        fallback: Any = None,
        max_pages: Any = None,
        max_mb: Any = None,
    ) -> dict[str, Any]:
        from core.pdf_support import FALLBACK_MODES, set_fallback_mode

        if fallback is not None:
            if fallback not in FALLBACK_MODES:
                return {"ok": False, "error": "pdf_fallback must be 'text' or 'images'"}
            self._prefs["pdf_fallback"] = fallback
        for key, value, ceiling in (
            ("pdf_max_pages", max_pages, 100),
            ("pdf_max_mb", max_mb, 10),
        ):
            if value is None:
                continue
            try:
                self._prefs[key] = max(1, min(int(value), ceiling))
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a number"}
        self._save_prefs()
        settings = self.pdf_settings()
        set_fallback_mode(settings["pdf_fallback"])  # engines read the module global
        return {"ok": True, **settings}


    def set_model_key(self, api_key: str) -> dict[str, Any]:
        """Persist the model API key to the SecretStore (0600). The new provider client is
        built lazily on the next turn, so it picks the key up without a restart."""
        api_key = (api_key or "").strip()
        if not api_key:
            return {"ok": False, "error": "empty api key"}
        # Merge, don't replace: the profile may also hold a custom endpoint (base_url).
        profile = dict(self.secrets.get(provider_profile_key("openai")) or {})
        profile.update({"type": "api_key", "api_key": api_key})
        profile.setdefault("name", "openai")
        profile.setdefault("protocol", "openai")
        profile.setdefault("api_mode", "responses")
        self.secrets.put(provider_profile_key("openai"), profile)
        self._refresh_provider("openai")  # rebuild the OpenAI client with the new key
        return {"ok": True, **self.get_settings()}


    def set_default_model(self, model: str) -> dict[str, Any]:
        """Set + persist the default model for new sessions (the UI pre-selects it)."""
        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "empty model"}
        self.model = model
        self._prefs["default_model"] = model
        self._save_prefs()
        return {"ok": True, **self.get_settings()}


    def set_language(self, value: str | None) -> dict[str, Any]:
        """Set + persist the UI/agent language (a Locale like `zh-CN` or `en-US`), or clear it
        (None → the GUI falls back to its own default). Unknown values are stored as-is so the
        GUI can decide; only the two supported Locales are ever written by the UI.
        """
        value = (value or "").strip() or None
        if value:
            self._prefs["language"] = value
        else:
            self._prefs.pop("language", None)
        self._save_prefs()
        return {"ok": True, **self.get_settings()}


    def set_onboarded(self, value: bool = True) -> dict[str, Any]:
        """Record that first-run setup is complete (so it isn't shown again)."""
        self._prefs["onboarded"] = bool(value)
        self._save_prefs()
        return {"ok": True, "onboarded": bool(value)}


    # -- provider proxy ---------------------------------------------------------
    def provider_complete(self, model, messages, tools=None):
        return self.provider.complete(model=model, messages=messages, tools=tools)


    def _refresh_provider(self, name: str | None = None) -> None:
        """Drop the router's cached client(s) so the next turn rebuilds with fresh config.
        No-op for an injected non-router provider (tests)."""
        invalidate = getattr(self.provider, "invalidate", None)
        if callable(invalidate):
            invalidate(name)
