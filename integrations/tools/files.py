"""Line-numbered file reading for the controlled worker boundary.

The toolkit's `read_file` returns raw text (the agent can't cite path:line without
counting) and raises outright on large files (the agent errors and guesses). This one
returns `cat -n`-style numbered lines, windows big files instead of failing, and tells
the agent how to continue reading. Read-only, workspace-scoped.
"""

# (tool-builder module: attaches aisuite's dynamic metadata attributes
# (__aisuite_tool_metadata__ / __delta_schema__) to plain functions 鈥?
# the framework's plugin protocol, not a type error.)

from __future__ import annotations

from pathlib import Path
from typing import Any

from integrations.tools import metadata as ai

from integrations.tools.metadata import attach_tool_metadata

_DEFAULT_MAX_LINES = 2000
_DEFAULT_LINES_WINDOW = 100  # tighter than read_file so "give me a slice" stays cheap
_MAX_LINE_CHARS = 500

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file, returning numbered lines ('   12\ttext') so code can be "
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

_READ_FILE_LINES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file_lines",
        "description": (
            "Read a small window of lines from a text file (default 100, max 500) and "
            "return them numbered so they can be cited as path:line. Use for quick slices; "
            "for full reads use read_file. Read-only."
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
                    "description": f"How many lines (default {_DEFAULT_LINES_WINDOW}).",
                },
            },
            "required": ["path"],
        },
    },
}


def file_tools(
    workspace: str,
    *,
    roots: list[Any] | None = None,
) -> list:
    """Build the read_file / read_file_lines tools bound to a workspace.

    ``roots`` (P2 follow-up A) makes the reader multi-root aware: when
    provided, both tools accept absolute paths and resolve them against
    any root in the list. The result carries the actual relative path and
    line window so the Rust Runtime can register an authoritative citation.
    """
    root = Path(workspace).resolve()

    resolved_roots: list[Path] = []
    if roots:
        resolved_roots = [Path(getattr(r, "path", r)).resolve() for r in roots]

    def _windowed_read(
        path: str,
        start_line: int,
        max_lines: int,
        default_max_lines: int,
        hard_max_lines: int,
    ) -> dict[str, Any]:
        """Shared read-window implementation for read_file and read_file_lines.

        Both tools share the same multi-root path resolver, the same
        Source/Citation chokepoint, and the same `lines` citation shape. They
        only differ in their default window size (`read_file` reads big,
        `read_file_lines` reads a small slice) and in their hard cap.
        """
        start = start_line if isinstance(start_line, int) and start_line > 0 else 1
        n = (
            max_lines
            if isinstance(max_lines, int) and max_lines > 0
            else default_max_lines
        )
        n = min(n, hard_max_lines)

        # Multi-root: accept absolute paths and resolve against any root.
        # Single-root: relative paths only, reject escapes.
        matching_root: Path | None = None
        if resolved_roots:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = (root / path).resolve()
            for r in resolved_roots:
                try:
                    target.resolve().relative_to(r)
                    matching_root = r
                    break
                except ValueError:
                    continue
            if matching_root is None:
                return {"error": "path escapes every known root"}
            target = target.resolve()
        else:
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
                        text = text[:_MAX_LINE_CHARS] + "…(line truncated)"
                    selected.append(f"{i:>6}\t{text}")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}

        end = start + len(selected) - 1 if selected else start - 1
        # Path in the result is relative to whichever root matched
        # (multi-root) or to the single workspace root (single-root).
        base = matching_root if matching_root is not None else root
        try:
            rel = str(target.relative_to(base))
        except ValueError:
            rel = str(target)
        result: dict[str, Any] = {
            "path": rel,
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

    def read_file(
        path: str,
        start_line: int = 1,
        max_lines: int = _DEFAULT_MAX_LINES,
    ) -> dict[str, Any]:
        return _windowed_read(
            path, start_line, max_lines, _DEFAULT_MAX_LINES, _DEFAULT_MAX_LINES
        )

    def read_file_lines(
        path: str,
        start_line: int = 1,
        max_lines: int = _DEFAULT_LINES_WINDOW,
    ) -> dict[str, Any]:
        # `read_file_lines` is the small-window sibling of `read_file`. Same
        # path-resolution rules, same Source/Citation chokepoint, same kind =
        # "lines" citation; only the default + hard cap are tighter.
        return _windowed_read(
            path, start_line, max_lines, _DEFAULT_LINES_WINDOW, _MAX_LINE_CHARS
        )

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

    read_file_lines.__name__ = "read_file_lines"
    read_file_lines.__doc__ = _READ_FILE_LINES_SCHEMA["function"]["description"]
    attach_tool_metadata(
        read_file_lines,
        schema=_READ_FILE_LINES_SCHEMA,
        metadata=ai.ToolMetadata(
            name="read_file_lines",
            category="filesystem",
            risk_level="low",
            capabilities=["read_file_lines"],
            requires_approval=False,
        ),
    )

    return [read_file, read_file_lines]
