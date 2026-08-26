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
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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

# Built-in date/time numFmt ids (their format codes are fixed by the OOXML spec);
# custom formats supply their own code in styles.xml, parsed below.
_BUILTIN_TEMPORAL_CODES = {
    14: "mm-dd-yy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm am/pm",
    19: "h:mm:ss am/pm",
    20: "h:mm",
    21: "h:mm:ss",
    22: "m/d/yy h:mm",
    45: "mm:ss",
    46: "[h]:mm:ss",
    47: "mmss.0",
}
# A style index → numFmtId, resolved once per workbook.
_TEMPORAL_TOKEN_RE = re.compile(r"[ydhs]", re.IGNORECASE)


def _is_temporal_code(code: str) -> bool:
    """True when a format code means date/time rather than a plain number/text.
    Any date/time token (y/d/h/s) outside a quoted literal flags temporal; pure
    numeric formats ('0.00', '#,##0', 'General', '@') contain none."""
    clean = re.sub(r'"[^"]*"', "", code or "")
    clean = re.sub(r"\[[^\]]*\]", "", clean)
    return bool(_TEMPORAL_TOKEN_RE.search(clean))


def _serial_to_render(value: float, *, has_date: bool, has_time: bool) -> str:
    """Excel serial number → a readable date/time string.

    Excel's epoch is 1899-12-30 (with the 1900 leap-year quirk); the common
    approximation 1899-12-30 + N days is exact for every modern date (>= 1900-03-01).
    We deliberately render a canonical YYYY-MM-DD HH:MM:SS rather than reproduce the
    workbook's locale/code formatting — the goal is "not a raw serial number".
    """
    base = datetime(1899, 12, 30) + timedelta(days=float(value))
    if has_date and has_time:
        return base.strftime("%Y-%m-%d %H:%M:%S")
    if has_date:
        return base.strftime("%Y-%m-%d")
    return base.strftime("%H:%M:%S")


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


def _read_styles(zf: zipfile.ZipFile) -> tuple[list[Any], dict[int, str]]:
    """Parse xl/styles.xml into (style_index → numFmtId, custom numFmtId → code).

    This is what turns a numeric cell into a date: the cell's `s` attribute indexes
    <cellXfs><xf numFmtId="…">, and that id is either a built-in date format or a
    custom one whose code is declared under <numFmts>. Without it every date lands
    as a raw serial number.
    """
    try:
        info = zf.getinfo("xl/styles.xml")
    except KeyError:
        return [], {}
    if info.file_size > MAX_MEMBER_BYTES:
        raise SheetParseError("styles part too large")
    try:
        root = ET.fromstring(zf.read(info))
    except ET.ParseError:
        return [], {}
    custom: dict[int, str] = {}
    for nf in root.findall(".//{*}numFmt"):
        try:
            nid = int(nf.get("numFmtId") or "")
        except (TypeError, ValueError):
            continue
        custom[nid] = nf.get("formatCode") or ""
    xfs: list[Any] = []
    for xf in root.findall(".//{*}cellXfs/{*}xf"):
        try:
            xfs.append(int(xf.get("numFmtId") or "0"))
        except (TypeError, ValueError):
            xfs.append(0)
    return xfs, custom


def _parse_sheet(
    zf: zipfile.ZipFile,
    member: str,
    shared: list[str],
    styles: tuple[list[Any], dict[int, str]],
) -> dict[str, Any]:
    xfs, custom_codes = styles
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
                text: Any
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
                    if t == "str":
                        text = raw  # formula string result — never numeric
                    else:
                        num = float(raw) if t == "n" else raw
                        # A numeric cell with a date/time format renders as a date.
                        code = None
                        try:
                            nid = xfs[int(c.get("s", "0"))]
                        except (ValueError, IndexError):
                            nid = 0
                        code = custom_codes.get(nid) or _BUILTIN_TEMPORAL_CODES.get(nid)
                        if code is not None and isinstance(num, (int, float)) and _is_temporal_code(code):
                            has_time = bool(re.search(r"[hs]", code, re.IGNORECASE))
                            has_date = bool(re.search(r"[yd]", code, re.IGNORECASE))
                            # A pure time (e.g. 'h:mm') has no date tokens; a pure date
                            # has no time tokens. Detect each independently.
                            text = _serial_to_render(num, has_date=has_date, has_time=has_time)
                        else:
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
            styles = _read_styles(zf)
            sheets_out: list[dict[str, Any]] = []
            for name, member in _worksheet_targets(zf)[:MAX_SHEETS]:
                parsed = _parse_sheet(zf, member, shared, styles)
                sheets_out.append({"name": name, **parsed})
            return {"sheets": sheets_out}
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise SheetParseError(f"not a readable .xlsx workbook: {exc}") from exc
