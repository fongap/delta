"""Small, dependency-free metadata boundary for controlled Python tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, TypeVar


ToolRiskLevel: TypeAlias = Literal["low", "medium", "high"]
ToolCallable = TypeVar("ToolCallable", bound=Callable[..., Any])


@dataclass
class ToolMetadata:
    name: str | None = None
    category: str | None = None
    risk_level: ToolRiskLevel = "low"
    capabilities: list[str] = field(default_factory=list)
    requires_approval: bool = False
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def tool(fn: ToolCallable, *, metadata: ToolMetadata | None = None) -> ToolCallable:
    """Attach declarative metadata without adding execution or policy behavior."""
    if metadata is not None:
        setattr(fn, "__delta_tool_metadata__", metadata)
    return fn


def attach_tool_metadata(
    fn: ToolCallable,
    *,
    schema: dict[str, Any] | None = None,
    metadata: ToolMetadata | None = None,
) -> ToolCallable:
    if schema is not None:
        setattr(fn, "__delta_schema__", schema)
    return tool(fn, metadata=metadata)


def get_tool_metadata(fn: Callable[..., Any]) -> ToolMetadata | None:
    metadata = getattr(fn, "__delta_tool_metadata__", None)
    return metadata if isinstance(metadata, ToolMetadata) else None
