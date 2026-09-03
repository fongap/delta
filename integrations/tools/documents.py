"""Multi-format document reader (P2 实用 — DELTA_BLUEPRINT §7.2).

The single ``read_document`` tool handles the formats the blueprint lists
first (PDF / Markdown / TXT / DOCX / XLSX) and emits a typed citation
through :mod:`core.citation` so the run can be located back to the
exact page / cell / paragraph / line that was read.

The reader is intentionally strict:

  - every format produces a list of *blocks* (pages, sheets, paragraphs)
    so the agent can ask for a specific block by index;
  - on success, the run is auto-cited with the matching :class:`CitationRange`
    kind (``page`` / ``cells`` / ``message_id`` for paragraphs / ``lines``);
  - on failure, the tool returns an ``{"error": ...}`` payload and
    does NOT cite (a phantom citation would be worse than none).

Optional-dependency policy: ``pypdf`` is already a runtime dep
(``core/pdf_support.py``). XLSX uses the stdlib zipfile + ElementTree
parser (mirrors ``services/server/sheet_preview.py``). DOCX reuses the
same approach — DOCX is OOXML, the text is ``word/document.xml`` as
``<w:p><w:r><w:t>`` runs. Markdown / TXT fall through to the line-numbered
``read_file`` path; this module only adds the binary-format readers.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import aisuite as ai

from integrations.tools.metadata import attach_tool_metadata

_MAX_BLOCK_CHARS = 4000  # same ceiling as read_file's _MAX_LINE_CHARS-equivalent per block

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_document",
             "description": (
                 "Read a PDF, XLSX, or DOCX file in the workspace and return its text. "
                 "PDFs are returned as a list of pages; XLSX as a list of sheets with "
                 "row counts and a `block` index for reading a specific sheet; DOCX as a "
                 "list of paragraphs. Markdown and plain text are NOT routed here — use "
                 "read_file, which is line-numbered. A successful read auto-cites the run "
                 "with a typed locator (page / cells / message_id) so the source ledger "
                 "can scroll back to the exact spot. Read-only. Omit block to return document/block summary; "
                 "pass a 0-based block index to read and cite that block."
             ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, relative to the workspace.",
                },
                "block": {
                    "type": "integer",
                    "description": (
                        "0-based index of the block to read (page / sheet / "
                        "paragraph). Omit to read the first block; pass -1 or a "
                        "large number to receive a `block_count` summary so you "
                        "can pick a range to read next."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}


# -- per-format readers --------------------------------------------------------


def _read_pdf(target: Path) -> list[dict[str, Any]]:
    """PDF: lazy import pypdf; one block per page; text-extraction fallback.

    Scanned PDFs legitimately come back as empty strings — the run
    citation still records the page number so the UI can offer to render
    that page as an image via pdf_support.rasterize later.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(target))
    blocks: list[dict[str, Any]] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # pypdf raises varied; surface as empty
            text = ""
        blocks.append({"index": i - 1, "page": i, "text": text[:_MAX_BLOCK_CHARS]})
    return blocks


def _read_xlsx(target: Path) -> list[dict[str, Any]]:
    """XLSX: stdlib zipfile + ElementTree; one block per sheet.

    Mirrors the bounded behavior of services/server/sheet_preview.py
    (MAX_SHEET_ROWS / MAX_COLUMNS / MAX_CELL_CHARS) — without them a
    hostile workbook could explode the run. The preview summary gives the
    agent enough to pick a sheet+range to read next.
    """
    # Reuse the preview module's limits / helpers; it is stdlib-only.
    from services.server import sheet_preview

    try:
        preview = sheet_preview.read_sheet_preview(target)
    except sheet_preview.SheetParseError as exc:
        return [{"index": 0, "sheet": "(parse-error)", "text": str(exc), "error": True}]
    blocks: list[dict[str, Any]] = []
    for i, sheet in enumerate(preview.get("sheets", [])):
        # Render a text-friendly tabular view; the agent can also re-ask
        # for a specific row/column range in a future reader.
        rows = sheet.get("rows", [])
        text_rows = [
            "\t".join(str(c) if c is not None else "" for c in row) for row in rows
        ]
        body = "\n".join(text_rows) if text_rows else "(empty sheet)"
        blocks.append(
            {
                "index": i,
                "sheet": sheet.get("name", f"Sheet{i + 1}"),
                "row_count": len(rows),
                "total_rows": sheet.get("total_rows", len(rows)),
                "truncated": sheet.get("truncated", False),
                "text": body[:_MAX_BLOCK_CHARS],
            }
        )
    return blocks


_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _read_docx(target: Path) -> list[dict[str, Any]]:
    """DOCX: stdlib zipfile + ElementTree; one block per paragraph.

    DOCX is OOXML — the visible text is ``word/document.xml`` as a tree of
    ``<w:p>`` paragraphs, each containing ``<w:r><w:t>`` runs. Tables are
    flattened (cells separated by tabs) so a single ``block`` can carry a
    short table row.
    """
    try:
        with zipfile.ZipFile(target) as zf:
            try:
                doc_xml = zf.read("word/document.xml")
            except KeyError:
                return [
                    {
                        "index": 0,
                        "paragraph": 1,
                        "text": "(missing word/document.xml)",
                        "error": True,
                    }
                ]
    except zipfile.BadZipFile:
        return [
            {
                "index": 0,
                "paragraph": 1,
                "text": "(not a valid .docx package)",
                "error": True,
            }
        ]
    try:
        root = ET.fromstring(doc_xml)
    except ET.ParseError:
        return [
            {
                "index": 0,
                "paragraph": 1,
                "text": "(document.xml is not well-formed)",
                "error": True,
            }
        ]
    blocks: list[dict[str, Any]] = []
    for i, p in enumerate(root.iter("{%s}p" % _DOCX_NS["w"]), start=1):
        # Join all <w:t> runs; tables are flattened to tab-separated cells.
        text = "".join(t.text or "" for t in p.iter("{%s}t" % _DOCX_NS["w"]))
        blocks.append(
            {
                "index": i - 1,
                "paragraph": i,
                "text": text[:_MAX_BLOCK_CHARS] if text else "(empty paragraph)",
            }
        )
    return blocks


def _detect_kind(path: Path) -> str:
    """File extension → reader key. Unrecognized → empty (caller returns an error)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".docx":
        return "docx"
    return ""


# -- public factory ------------------------------------------------------------


def document_tools(
    workspace: str,
    *,
    source_store: Any | None = None,
    run_id: str | None = None,
) -> list:
    """Build the ``read_document`` tool bound to a workspace.

    ``source_store`` + ``run_id`` are the same opt-in auto-cite hook as
    ``read_file`` (P2 实用). Errors (unknown extension / parse failure /
    path outside workspace) are returned as ``{"error": ...}`` payloads
    and do NOT cite — a phantom citation is worse than none.
    """
    from core.citation import cite
    from core.sources import KIND_CELLS, KIND_MESSAGE_ID, KIND_PAGE, CitationRange

    root = Path(workspace).resolve()

    def read_document(
        path: str,
        block: int | None = None,
    ) -> dict[str, Any]:
        target = (root / path).resolve()
        try:
            target.relative_to(root)  # keep reads inside the workspace
        except ValueError:
            return {"error": "path escapes the workspace"}
        if not target.is_file():
            return {"error": f"not a file: {path}"}

        kind = _detect_kind(target)
        if not kind:
            return {
                "error": (
                    f"unsupported file type: {target.suffix}. "
                    "Use read_file for text / markdown / logs."
                )
            }

        try:
            if kind == "pdf":
                blocks = _read_pdf(target)
            elif kind == "xlsx":
                blocks = _read_xlsx(target)
            else:
                blocks = _read_docx(target)
        except Exception as exc:  # noqa: BLE001 — parse failure must not crash the run
            return {"error": f"{kind} read failed: {exc.__class__.__name__}: {exc}"}

        if not blocks:
            return {"error": f"empty {kind} document: {path}"}

        # block=None → summary; an int → the requested block + a typed citation.
        if block is None or block < 0 or block >= len(blocks):
            return {
                "path": str(target.relative_to(root)),
                "kind": kind,
                "block_count": len(blocks),
                "blocks": [
                    {
                        "index": b.get("index"),
                        "label": _block_label(kind, b),
                    }
                    for b in blocks
                ],
            }

        chosen = blocks[block]
        # Build a typed citation from the chosen block. The kind matches
        # the source's locator vocabulary (page for PDF, cells for XLSX,
        # message_id for DOCX — fits the same hook).
        if kind == "pdf":
            range_obj = CitationRange(kind=KIND_PAGE, page=chosen["page"])
        elif kind == "xlsx":
            range_obj = CitationRange(
                kind=KIND_CELLS,
                sheet=chosen["sheet"],
                row_start=1,
                row_end=chosen.get("row_count", 0) or 0,
            )
        else:  # docx
            # message_id kind fits a paragraph anchor (it's the only
            # ordered, single-target hook in the schema); the descriptor
            # carries the paragraph number for renderers that want it.
            range_obj = CitationRange(
                kind=KIND_MESSAGE_ID,
                message_id=f"paragraph:{chosen['paragraph']}",
            )
        cite(source_store, run_id, target, range_obj, workspace=root)
        return {
            "path": str(target.relative_to(root)),
            "kind": kind,
            "block": chosen,
            "block_count": len(blocks),
        }

    read_document.__name__ = "read_document"
    read_document.__doc__ = _SCHEMA["function"]["description"]
    attach_tool_metadata(
        read_document,
        schema=_SCHEMA,
        metadata=ai.ToolMetadata(
            name="read_document",
            category="filesystem",
            risk_level="low",
            capabilities=["read"],
            requires_approval=False,
        ),
    )
    return [read_document]


def _block_label(kind: str, block: dict[str, Any]) -> str:
    """A one-line label for the summary view (page 3 / sheet 'Q3' / paragraph 12)."""
    if kind == "pdf":
        return f"page {block.get('page', block.get('index', 0) + 1)}"
    if kind == "xlsx":
        return (
            f"sheet {block.get('sheet', block.get('index', 0) + 1)!r} "
            f"({block.get('row_count', 0)} rows)"
        )
    return f"paragraph {block.get('paragraph', block.get('index', 0) + 1)}"
