"""Line-numbered file reading (`read_file`) 鈥?replaces the aisuite toolkit's reader.

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
from typing import Any, Callable

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


def _make_citer(
    root: Path, source_store: Any, run_id: str
) -> Callable[[Path, int, int], None]:
    """Build a closure that captures the file and records a lines citation.

    Imported here (not at module top) so the SourceStore / CitationRange
    modules stay optional dependencies for callers that don't use the
    auto-cite hook. The actual capture+cite is delegated to
    :func:`core.citation.cite` so every reader (text, PDF, XLSX, ...) goes
    through the same chokepoint.
    """
    from core.citation import cite
    from core.sources import KIND_LINES, CitationRange

    def _cite(target: Path, start_line: int, end_line: int) -> None:
        # An empty window (start > end, e.g. an empty file or a read past the
        # last line) is still a successful "I read this file" event — record
        # just the start so the UI can show the file was opened.
        if end_line < start_line:
            range_obj: CitationRange = CitationRange(kind=KIND_LINES, start=start_line)
        else:
            range_obj = CitationRange(
                kind=KIND_LINES, start=start_line, end=end_line
            )
        cite(source_store, run_id, target, range_obj, workspace=root)

    return _cite


def _make_multiroot_citer(
    roots: list[Any], source_store: Any, run_id: str
) -> Callable[[Path, int, int], None]:
    """Build a closure that cites a read against whichever root the file
    actually lives in (not the primary root). Falls back to the primary
    root for path normalization when the file is outside every known
    root -- the cite hook is best-effort and must never raise out of a
    tool call.
    """
    from core.citation import cite
    from core.sources import KIND_LINES, CitationRange

    resolved = [Path(getattr(r, "path", r)).resolve() for r in roots]

    def _cite(target: Path, start_line: int, end_line: int) -> None:
        if end_line < start_line:
            range_obj = CitationRange(kind=KIND_LINES, start=start_line)
        else:
            range_obj = CitationRange(
                kind=KIND_LINES, start=start_line, end=end_line
            )
        ws: Path | None = None
        for r in resolved:
            try:
                target.resolve().relative_to(r)
                ws = r
                break
            except ValueError:
                continue
        if ws is None:
            ws = resolved[0] if resolved else None
        cite(source_store, run_id, target, range_obj, workspace=ws)

    return _cite


def file_tools(
    workspace: str,
    *,
    source_store: Any | None = None,
    run_id: str | None = None,
    roots: list[Any] | None = None,
) -> list:
    """Build the read_file tool bound to a workspace.

    ``source_store`` + ``run_id`` are an opt-in audit hook (P2 实用): every
    successful read is captured as a :class:`SourceRef` and cited under the
    run with a ``lines`` range covering the read window. Callers that don't
    pass them (e.g. existing tests, ad-hoc callers) get the original
    behavior unchanged -- the hook is purely additive.

    ``roots`` (P2 follow-up A) makes the reader multi-root aware: when
    provided, ``read_file`` accepts absolute paths and resolves them
    against any root in the list (mirroring aisuite's multi-root
    ``read_file``). The cite hook records the citation against whichever
    root the file actually lives in, not the primary.
    """
    root = Path(workspace).resolve()
    if roots:
        _cite = (
            _make_multiroot_citer(roots, source_store, run_id)
            if source_store and run_id
            else None
        )
    else:
        _cite = _make_citer(root, source_store, run_id) if source_store and run_id else None

    resolved_roots: list[Path] = []
    if roots:
        resolved_roots = [Path(getattr(r, "path", r)).resolve() for r in roots]

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
                        # proof that more content exists 鈥?no whole-file count.
                        has_more = True
                        break
                    text = line.rstrip("\n")
                    if len(text) > _MAX_LINE_CHARS:
                        text = text[:_MAX_LINE_CHARS] + "鈥?(line truncated)"
                    selected.append(f"{i:>6}\t{text}")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}

        end = start + len(selected) - 1 if selected else start - 1
        if _cite is not None:
            _cite(target, start, end)
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
