"""Line-numbered file reading (`read_file`) — replaces the aisuite toolkit's reader.

The toolkit's `read_file` returns raw text (the agent can't cite path:line without
counting) and raises outright on large files (the agent errors and guesses). This one
returns `cat -n`-style numbered lines, windows big files instead of failing, and tells
the agent how to continue reading. Read-only, workspace-scoped.
"""

# (tool-builder module: attaches aisuite's dynamic metadata attributes
# (__aisuite_tool_metadata__ / __delta_schema__) to plain functions —
# the framework's plugin protocol, not a type error.)

from __future__ import annotations

from pathlib import Path
from typing import Any

import aisuite as ai

from integrations.tools.metadata import attach_tool_metadata

_DEFAULT_MAX_LINES = 2000
_MAX_LINE_CHARS = 500

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file, returning numbered lines ('   12\\ttext') so code can be "
            "referenced as path:line. Large files are windowed: pass start_line to continue "
            "where the previous read stopped. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, relative to the workspace.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read, 1-based (default 1).",
                },
                "max_lines": {
                    "type": "integer",
                    "description": f"How many lines (default {_DEFAULT_MAX_LINES}).",
                },
            },
            "required": ["path"],
        },
    },
}


def file_tools(workspace: str) -> list:
    root = Path(workspace).resolve()

    def read_file(
        path: str,
        start_line: int = 1,
        max_lines: int = _DEFAULT_MAX_LINES,
    ) -> dict[str, Any]:
        start = start_line if isinstance(start_line, int) and start_line > 0 else 1
        n = (
            max_lines
            if isinstance(max_lines, int) and max_lines > 0
            else _DEFAULT_MAX_LINES
        )
        n = min(n, _DEFAULT_MAX_LINES)
        target = (root / path).resolve()
        try:
            target.relative_to(root)  # keep reads inside the workspace
        except ValueError:
            return {"error": "path escapes the workspace"}
        if not target.is_file():
            return {"error": f"not a file: {path}"}

        selected: list[str] = []
        has_more = False
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if i < start:
                        continue
                    if len(selected) >= n:
                        # Reaching one line past the window is the cheapest honest
                        # proof that more content exists — no whole-file count.
                        has_more = True
                        break
                    text = line.rstrip("\n")
                    if len(text) > _MAX_LINE_CHARS:
                        text = text[:_MAX_LINE_CHARS] + "… (line truncated)"
                    selected.append(f"{i:>6}\t{text}")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}

        end = start + len(selected) - 1 if selected else start - 1
        result: dict[str, Any] = {
            "path": str(target.relative_to(root)),
            "start_line": start,
            "end_line": end,
            # Windowed reads stop at the window edge (a huge file is never scanned
            # just to number its last line): callers page forward via has_more.
            "has_more": has_more,
            "content": "\n".join(selected),
        }
        if has_more:
            result["note"] = (
                f"showing lines {start}-{end}; "
                f"call again with start_line={end + 1} to continue"
            )
        return result

    read_file.__name__ = "read_file"
    read_file.__doc__ = _SCHEMA["function"]["description"]
    attach_tool_metadata(
        read_file,
        schema=_SCHEMA,
        metadata=ai.ToolMetadata(
            name="read_file",
            category="filesystem",
            risk_level="low",
            capabilities=["read"],
            requires_approval=False,
        ),
    )
    return [read_file]
