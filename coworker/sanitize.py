"""SensitiveDataSanitizer — one recursive scrubbing policy, shared everywhere.

Audit rows, Run Event Ledger payloads, and any future log sinks must agree on what
"no secrets in persisted data" means. This module is that single definition:

- secret-shaped KEYS (token/password/api_key/…) are fully redacted, at any nesting
  depth, in dicts and lists;
- credential-bearing HTTP headers (Authorization, Cookie, …) are redacted by name,
  whatever dict they ride in;
- URL query parameters that commonly carry credentials are stripped inside string
  values (`https://host/x?token=t` → `https://host/x?token=[redacted]`);
- body-ish keys (body/content/html) are redacted wholesale: free text is where
  secrets hide without ever announcing themselves.

Truncation and preview shaping are presentation concerns and stay with the callers
(e.g. audit's result previews); this module only decides WHAT must not persist.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

SECRET_KEY_MARKERS = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "access_token",
    "bot_token",
    "app_token",
    "refresh_token",
    "credential",
    "private_key",
    "raw",
)

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
    }
)

BODY_KEYS = ("body", "content", "html")

_URL_CRED_PARAMS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "password",
        "credential",
        "sig",
        "signature",
        "auth",
    }
)

_REDACTED = "[redacted]"
_REDACTED_BODY = "[redacted body]"
_REDACTED_INPUT = "[redacted input]"


def is_secret_key(key: Any) -> bool:
    """True when a mapping key names something secret-shaped."""
    lk = str(key).lower()
    return (
        any(marker in lk for marker in SECRET_KEY_MARKERS)
        or lk in SENSITIVE_HEADERS
        # header dicts nest values under lowercase names; also catch
        # "headers.authorization"-style paths and list items like "headers[cookie]"
        or any(lk.endswith(h) or lk.endswith("[" + h + "]") for h in SENSITIVE_HEADERS)
    )


def is_body_key(key: Any) -> bool:
    lk = str(key).lower()
    return any(b == lk or lk.endswith("_" + b) for b in BODY_KEYS)


def redact_url_credentials(value: str) -> str:
    """Strip credential-bearing query parameters from an http(s) URL string.

    Only well-formed absolute http(s) URLs are rewritten; anything else passes
    through unchanged (deterministic — no guessing about non-URL strings).
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return value
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return value
    if not pairs:
        return value
    scrubbed = [
        (k, _REDACTED if k.lower() in _URL_CRED_PARAMS else v) for k, v in pairs
    ]
    if scrubbed == pairs:
        return value
    query = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='[]')}" for k, v in scrubbed
    )
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, parts.fragment)
    )


def sanitize_value(value: Any, *, typed_input_keys: frozenset[str] = frozenset()) -> Any:
    """Recursively apply the shared scrubbing policy to any JSON-ish value."""
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            lk = str(key).lower()
            if is_secret_key(key):
                out[key] = _REDACTED
            elif lk in typed_input_keys:
                out[key] = _REDACTED_INPUT
            elif is_body_key(key):
                out[key] = _REDACTED_BODY
            else:
                out[key] = sanitize_value(item, typed_input_keys=typed_input_keys)
        return out
    if isinstance(value, (list, tuple)):
        sanitized = [sanitize_value(v, typed_input_keys=typed_input_keys) for v in value]
        return sanitized if isinstance(value, list) else type(value)(sanitized)
    if isinstance(value, str):
        return redact_url_credentials(value)
    return value


def sanitize_payload(payload: Any) -> Any:
    """Scrub a ledger/log payload. Kept as a named entry point so call sites read
    honestly: this is the one shared SensitiveDataSanitizer."""
    return sanitize_value(payload)
