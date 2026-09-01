"""Typed boundary for aisuite's runtime tool metadata attributes.

aisuite discovers tool metadata and Delta's explicit schema through attributes attached
to callables.  Keep that dynamic framework interop in one small module so tool builders
remain fully checked without file-level Pyright suppressions.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import aisuite as ai


ToolCallable = TypeVar("ToolCallable", bound=Callable[..., Any])


def attach_tool_metadata(
    fn: ToolCallable,
    *,
    schema: dict[str, Any] | None = None,
    metadata: ai.ToolMetadata | None = None,
) -> ToolCallable:
    if schema is not None:
        setattr(fn, "__delta_schema__", schema)
    if metadata is not None:
        setattr(fn, "__aisuite_tool_metadata__", metadata)
    return fn


def get_tool_metadata(fn: Callable[..., Any]) -> ai.ToolMetadata | None:
    metadata = getattr(fn, "__aisuite_tool_metadata__", None)
    return metadata if isinstance(metadata, ai.ToolMetadata) else None
