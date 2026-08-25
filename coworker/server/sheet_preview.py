"""Server-side spreadsheet preview parsing (.xlsx).

The GUI used to parse workbooks client-side with npm `xlsx` (known Prototype
Pollution + ReDoS, unfixed on npm). Parsing now happens here and the frontend
only renders the bounded JSON preview this module produces.

openpyxl is not a project dependency, so .xlsx is parsed with the stdlib
(zipfile + xml.etree) against the OOXML format, which is well-defined. Legacy
binary .xls (BIFF) has no stdlib-parseable structure and is rejected with a
friendly error instead ("Open in default app" still works for those).

All output is size-bounded so a hostile workbook cannot blow up the response:
row / column / cell-text caps plus an uncompressed-member guard against zip
bombs.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

# Frontend GridTable shows rows[0] as the header + up to 500 body rows; mirror
# that cap here (501 = 1 header row + 500 body rows) and report total_rows so
# the UI can still show the "showing X of Y rows" note.
MAX_SHEET_ROWS = 501
MAX_COLUMNS = 256
MAX_CELL_CHARS = 1000
MAX_SHEETS = 30
# A 25MB xlsx can legitimately decompress to far more; refuse absurd members.
MAX_MEMBER_BYTES = 256 * 1024 * 1024

_COL_RE = re.compile(r"^([A-Z]+)")


class SheetParseError(ValueError):
    """A workbook could not be parsed (corrupt file or unsupported format)."""


def _col_index(ref: str) -> int:
    """'BC12' -> 54 (0-based column index from a cell reference)."""
    m = _COL_RE.match(ref)
    if not m:
        return -1
    idx = 0
    for ch in m.group(1):
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _cell_text(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_CELL_CHARS]
    return value


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        info = zf.getinfo("xl/sharedStrings.xml")
    except KeyError:
        return []
    if info.file_size > MAX_MEMBER_BYTES:
        raise SheetParseError("shared string table too large")
    root = ET.fromstring(zf.read(info))
    strings: list[str] = []
    for si in root.findall("{*}si"):
        # <si> is either a single <t> or rich-text runs of several <r><t>.
        strings.append("".join(t.text or "" for t in si.findall(".//{*}t")))
    return strings


def _worksheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(sheet name, member path)] in workbook order, via workbook.xml + rels."""
    wb = ET.fromstring(_member(zf, "xl/workbook.xml"))
    rels = ET.fromstring(_member(zf, "xl/_rels/workbook.xml.rels"))
    rel_target = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels.findall(".//{*}Relationship")
    }
    out: list[tuple[str, str]] = []
    for sheet in wb.findall(".//{*}sheet"):
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_target.get(rid, "")
        if not target:
            continue
        # Rel targets are relative to xl/; a leading slash is package-absolute.
        if target.startswith("/"):
            member = target.lstrip("/")
        else:
            member = "xl/" + target
        name = sheet.get("name") or f"Sheet{len(out) + 1}"
        out.append((name, member))
    return out


def _member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise SheetParseError(f"missing workbook part: {name}") from exc
    if info.file_size > MAX_MEMBER_BYTES:
        raise SheetParseError(f"workbook part too large: {name}")
    return zf.read(info)


def _parse_sheet(
    zf: zipfile.ZipFile,
    member: str,
    shared: list[str],
) -> dict[str, Any]:
    root = ET.fromstring(_member(zf, member))
    data = root.find("{*}sheetData")
    rows: list[list[Any]] = []
    total_rows = 0
    if data is not None:
        for row_el in data.findall("{*}row"):
            total_rows += 1
            if len(rows) >= MAX_SHEET_ROWS:
                continue  # keep counting totals without materializing more rows
            cells: list[Any] = []
            for c in row_el.findall("{*}c"):
                ref = c.get("r") or ""
                col = _col_index(ref)
                if col < 0 or col >= MAX_COLUMNS:
                    continue
                t = c.get("t", "n")
                if t == "s":
                    v = c.findtext("{*}v")
                    text = shared[int(v)] if v and v.isdigit() and int(v) < len(shared) else ""
                elif t == "inlineStr":
                    text = "".join(x.text or "" for x in c.findall(".//{*}t"))
                elif t == "b":
                    text = "TRUE" if c.findtext("{*}v") == "1" else "FALSE"
                else:  # numbers and formula result strings both live in <v>
                    raw = c.findtext("{*}v")
                    if raw is None:
                        continue
                    num = float(raw) if t == "n" else raw
                    text = int(num) if isinstance(num, float) and num.is_integer() else num
                while len(cells) <= col:
                    cells.append("")
                cells[col] = _cell_text(text)
            rows.append(cells)
    truncated = total_rows > len(rows)
    return {"rows": rows, "total_rows": total_rows, "truncated": truncated}


def read_sheet_preview(path: Path) -> dict[str, Any]:
    """Parse an .xlsx file into a bounded JSON preview payload.

    Returns `{"sheets": [{"name", "rows", "total_rows", "truncated"}]}`.
    Raises SheetParseError for corrupt files and legacy .xls.
    """
    if path.suffix.lower() == ".xls":
        raise SheetParseError(
            "legacy .xls preview is no longer supported inline — use Reveal to open it"
        )
    try:
        with zipfile.ZipFile(path) as zf:
            shared = _read_shared_strings(zf)
            sheets_out: list[dict[str, Any]] = []
            for name, member in _worksheet_targets(zf)[:MAX_SHEETS]:
                parsed = _parse_sheet(zf, member, shared)
                sheets_out.append({"name": name, **parsed})
            return {"sheets": sheets_out}
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise SheetParseError(f"not a readable .xlsx workbook: {exc}") from exc
