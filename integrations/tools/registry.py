"""Tool registry — wraps callables (incl. aisuite toolkit tools) into a registry the
runtime owns: JSON schemas for the model, plus execution. Permission checks live in the
PermissionEngine and are applied by the turn engine, not here.

Schema generation is reused from aisuite (`Tools`) so we don't reimplement
docstring/type-hint → JSON-schema extraction.

Schema slimming (v0.3.0 P1): the generated schemas ride the WHOLE docstring into the
model-facing `description`, and aisuite adds verbose titles/enums. On shared/free
gateways every redundant token of a tool schema costs prompt-processing time, so
`register` stores a SLIM copy (trimmed descriptions, dropped `title`/redundant
`additionalProperties`, capped enums) while keeping the structural keys the runtime
reads (`name`, `parameters.properties`, `required`) byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from aisuite.utils.tools import Tools

# -- schema slimming (v0.3.0 P1) -------------------------------------------------

# Description ceiling for the model-facing function/parameter descriptions. Long
# docstrings add prompt-processing time on every call without helping a modern model
# pick the tool; the first sentence is the load-bearing part.
_MAX_DESC_CHARS = 300
# Long enum lists add schema weight for little gain — cap them (the model can pass any
# value; the enum is a hint, not validation here).
_MAX_ENUM = 24
# `additionalProperties: false` is the schema default; aisuite emits it on every object.
# The model gains nothing from seeing it repeatedly.
_STRIP_DEFAULT_ADDL_PROPS = True


def _clip(text: str, limit: int = _MAX_DESC_CHARS) -> str:
    """Trim a description to the ceiling, cutting at a sentence boundary when possible.
    The boundary must not be too early — a single short sentence ahead of the cut would
    strip everything load-bearing (the previous bug cut 690 chars to 2)."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    # The last sentence boundary strictly inside the window, but not before the middle —
    # a cut below the halfway point keeps too little of the description.
    lo = max(limit // 2, 1)
    cut = text.rfind(". ", lo, limit)
    if cut > lo:
        return text[: cut + 1].rstrip()
    return text[: limit - 1].rstrip() + "…"


def _slim_param_schema(node: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    """One parameter/property node: clip description, drop title + default
    additionalProperties, cap enums (recursing into nested items)."""
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("title", "additionalProperties", "$schema", "$id"):
            continue
        if key == "additionalProperties" and _STRIP_DEFAULT_ADDL_PROPS:
            continue
        if key == "description" and isinstance(value, str):
            out[key] = _clip(value)
            continue
        if key == "enum" and isinstance(value, list):
            out[key] = value[:_MAX_ENUM]
            if len(value) > _MAX_ENUM:
                out[key].append(f"… (+{len(value) - _MAX_ENUM} more)")
            continue
        if key in ("items",) and isinstance(value, dict):
            out[key] = _slim_param_schema(value, depth=depth + 1)
            continue
        if isinstance(value, dict) and key == "properties" and depth < 3:
            out[key] = {
                sub: _slim_param_schema(sub_node, depth=depth + 1)
                for sub, sub_node in value.items()
            }
            continue
        out[key] = value
    return out


def slim_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A model-facing copy of an OpenAI-format function tool schema with the verbosity
    the model doesn't need stripped. Structure the runtime relies on (`function.name`,
    `function.parameters.properties/required/type`) is preserved verbatim; only prose
    and noise are trimmed. Idempotent."""
    fn = dict(schema.get("function") or {})
    if isinstance(fn.get("description"), str):
        fn["description"] = _clip(fn["description"])
    params = fn.get("parameters")
    if isinstance(params, dict):
        fn["parameters"] = _slim_param_schema(params)
    return {"type": schema.get("type", "function"), "function": fn}


@dataclass
class ToolSpec:
    name: str
    schema: dict[str, Any]  # OpenAI-format function tool schema (SLIM — model-facing)
    func: Callable[..., Any]
    metadata: Any = None  # aisuite ToolMetadata or None
    # The unslimmed schema as registered (what the model never needs but the runtime
    # may read for auditing/approval). None when the schema was already explicit+slim.
    raw_schema: dict[str, Any] | None = None
    # Injection category (core/tool_selection.py). Unset → resolved by name at
    # selection time; an explicit value here wins.
    category: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        func: Callable[..., Any],
        *,
        metadata: Any = None,
        schema: dict[str, Any] | None = None,
        slim: bool = True,
    ) -> ToolSpec:
        name = getattr(func, "__name__", None)
        if not name:
            raise ValueError("Tool function must have a __name__.")
        meta = metadata or getattr(func, "__aisuite_tool_metadata__", None)
        # Allow an explicit schema override (param or a `__delta_schema__` attribute)
        # for tools whose signature can't be auto-converted to a valid JSON schema.
        resolved_schema = (
            schema or getattr(func, "__delta_schema__", None) or _schema_for(func)
        )
        raw = None if not slim else resolved_schema
        stored_schema = slim_schema(resolved_schema) if slim else resolved_schema
        spec = ToolSpec(
            name=name,
            schema=stored_schema,
            func=func,
            metadata=meta,
            raw_schema=raw,
        )
        self._tools[name] = spec
        return spec

    def register_all(self, funcs: list[Callable[..., Any]]) -> None:
        for func in funcs:
            self.register(func)

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """Tool schemas for the model call. `names=None` keeps the historical
        everything-injection; a name list (per-call tool selection) filters to it —
        unknown names are skipped silently (the registry may have changed)."""
        if names is None:
            return [spec.schema for spec in self._tools.values()]
        return [self._tools[n].schema for n in names if n in self._tools]

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"Tool not registered: {name}")
        return spec.func(**(arguments or {}))


def _schema_for(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate one OpenAI-format tool schema via aisuite's schema generator."""
    return Tools([func]).tools(format="openai")[0]
