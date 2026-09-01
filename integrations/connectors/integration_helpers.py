"""Generic infrastructure helpers for the connector tools.

Split out of ``integration_tools.py`` (which is the per-vendor tool factory). These are the
vendor-agnostic plumbing — tool metadata/schema attachment, the HTTP client, HTML-to-text,
and small numeric/time utilities — shared by every connector's tools. Keeping them here
separates "how a tool is defined and shipped" from "what each vendor's tools do".

Pure mechanical move: each function's body is identical to what lived in integration_tools.py,
and the factory re-imports them by the same names, so no behavior changes.
"""

# (tool-builder plumbing: _attach stamps aisuite's dynamic metadata attributes
# (__aisuite_tool_metadata__ / __delta_schema__) onto plain functions —
# the framework's plugin protocol, not a type error.)

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Callable

from integrations.connectors.tool_defs import approval_for_tool
from integrations.web.guard import get_checked

import aisuite as ai

from integrations.tools.metadata import attach_tool_metadata


def _meta(
    name: str, *, approval: bool = False, capabilities: list[str] | None = None
):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities or ["integration"],
        requires_approval=approval,
    )


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _attach(
    fn: Callable[..., Any],
    schema: dict[str, Any],
    *,
    approval: bool = True,
    caps: list[str] | None = None,
):
    name = schema["function"]["name"]
    # §36: the tool registry's read/write kind overrides the call-site flag for
    # registered tools — connector READS never gate. The explicit arg only governs
    # tools without a registry entry.
    approval = approval_for_tool(name, default=approval)
    attach_tool_metadata(
        fn,
        schema=schema,
        metadata=_meta(name, approval=approval, capabilities=caps),
    )
    fn.__doc__ = schema["function"]["description"]
    return fn


def _request(
    method: str,
    url: str,
    *,
    headers=None,
    params=None,
    json=None,
    auth=None,
    check_addresses: bool = False,
) -> dict[str, Any]:
    """HTTP for the connectors.

    `check_addresses` is for URLs the *model* supplies (browser_read_url). It turns off
    automatic redirects and walks the chain through the address guard instead, so a public
    URL cannot 302 into loopback or the metadata endpoint. The vendor endpoints everything
    else in this module calls are hardcoded, so they skip the guard and its DNS lookup.
    """
    try:
        import httpx

        with httpx.Client(
            timeout=30.0, follow_redirects=not check_addresses
        ) as client:
            if check_addresses:
                if method.upper() != "GET":
                    return {"error": "address-checked requests must be GET"}
                try:
                    resp = get_checked(client, url)
                except PermissionError as exc:
                    return {"error": str(exc)}
            else:
                resp = client.request(
                    method, url, headers=headers, params=params, json=json, auth=auth
                )
            ctype = resp.headers.get("content-type", "")
            data: Any = resp.json() if "json" in ctype.lower() else resp.text
            if resp.status_code >= 400:
                return {"error": f"HTTP {resp.status_code}", "details": data}
            return {"ok": True, "data": data}
    except Exception as exc:
        return {"error": str(exc)}


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts))


def _now_ms() -> int:
    from time import time

    return int(time() * 1000)


def _clamp(n: Any, default: int = 10, ceiling: int = 20) -> int:
    return max(1, min(int(n or default), ceiling))
