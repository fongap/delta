"""Server-side spreadsheet preview parsing (P1 security fix).

The GUI used to parse workbooks client-side with npm `xlsx` (Prototype Pollution +
ReDoS). Parsing now lives in coworker/server/sheet_preview.py and these tests pin
the contract: multi-sheet structure, value kinds, size caps, and graceful errors
for corrupt / legacy files.

Fixtures are minimal hand-built OOXML packages — no spreadsheet dependency needed.
"""

from __future__ import annotations

import zipfile

from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app

# -- fixture builders -----------------------------------------------------------


def _col_letter(i: int) -> str:
    """0-based column index -> letters (0 -> 'A', 27 -> 'AB')."""
    out = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        out = chr(65 + rem) + out
    return out


def make_xlsx(path, sheets):
    """Build a minimal valid .xlsx.

    sheets: [(name, rows)]; each row is a list of cells where a cell is either
    None (gap) or (kind, value) with kind in {"s" (shared string), "n" (number),
    "inline" (inline string), "b" (boolean)}.
    """
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    shared: list[str] = []
    members: dict[str, str] = {}
    sheet_tags, rel_tags = [], []

    for idx, (name, rows) in enumerate(sheets, 1):
        member = f"xl/worksheets/sheet{idx}.xml"
        rel_tags.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        )
        row_tags = []
        for ri, row in enumerate(rows, 1):
            cell_tags = []
            for ci, spec in enumerate(row):
                if spec is None:
                    continue
                kind, val = spec
                ref = f"{_col_letter(ci)}{ri}"
                if kind == "s":
                    if val not in shared:
                        shared.append(val)
                    cell_tags.append(f'<c r="{ref}" t="s"><v>{shared.index(val)}</v></c>')
                elif kind == "inline":
                    cell_tags.append(
                        f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>'
                    )
                elif kind == "b":
                    cell_tags.append(
                        f'<c r="{ref}" t="b"><v>{1 if val else 0}</v></c>'
                    )
                else:  # "n"
                    cell_tags.append(f'<c r="{ref}"><v>{val}</v></c>')
            row_tags.append(f'<row r="{ri}">{"".join(cell_tags)}</row>')
        members[member] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_tags)}</sheetData></worksheet>'
        )
        sheet_tags.append(f'<sheet name="{name}" sheetId="{idx}" r:id="rId{idx}"/>')

    members["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{ns}" xmlns:r="{rns}"><sheets>{"".join(sheet_tags)}</sheets></workbook>'
    )
    members["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(rel_tags)}</Relationships>'
    )
    if shared:
        si_tags = "".join(f"<si><t>{s}</t></si>" for s in shared)
        members["xl/sharedStrings.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{ns}" count="{len(shared)}" uniqueCount="{len(shared)}">{si_tags}</sst>'
        )
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _client(tmp_path) -> TestClient:
    manager = SessionManager(workspace=tmp_path)
    return TestClient(create_app(manager))


def _make_xlsx_with_styles(path, *, numfmts, cell_xfs, rows):
    """Build a minimal .xlsx with a real styles.xml (date/time formatting).

    numfmts: [(numFmtId, formatCode)] custom formats.
    cell_xfs: list of numFmtIds — each becomes an <xf> in cellXfs (in order).
    rows: list of rows; each cell is (kind, value, xf_index) or (kind, value).
    """
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    members: dict[str, str] = {}
    sheet_tags, rel_tags = [], []
    for idx, (name, srows) in enumerate(rows, 1):
        member = f"xl/worksheets/sheet{idx}.xml"
        rel_tags.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        )
        row_tags = []
        for ri, row in enumerate(srows, 1):
            cell_tags = []
            for ci, spec in enumerate(row):
                if spec is None:
                    continue
                kind, val = spec[0], spec[1]
                xf = spec[2] if len(spec) > 2 else 0
                ref = f"{_col_letter(ci)}{ri}"
                style = f' s="{xf}"' if xf else ""
                if kind == "s":
                    cell_tags.append(
                        f'<c r="{ref}" t="s"{style}><v>{val}</v></c>'
                    )
                elif kind == "inline":
                    cell_tags.append(
                        f'<c r="{ref}" t="inlineStr"{style}><is><t>{val}</t></is></c>'
                    )
                elif kind == "b":
                    cell_tags.append(
                        f'<c r="{ref}" t="b"{style}><v>{1 if val else 0}</v></c>'
                    )
                else:  # "n"
                    cell_tags.append(f'<c r="{ref}"{style}><v>{val}</v></c>')
            row_tags.append(f'<row r="{ri}">{"".join(cell_tags)}</row>')
        members[member] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_tags)}</sheetData></worksheet>'
        )
        sheet_tags.append(f'<sheet name="{name}" sheetId="{idx}" r:id="rId{idx}"/>')

    members["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{ns}" xmlns:r="{rns}"><sheets>{"".join(sheet_tags)}</sheets></workbook>'
    )
    members["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(rel_tags)}</Relationships>'
    )
    fmt_tags = "".join(
        f'<numFmt numFmtId="{fid}" formatCode="{code}"/>' for fid, code in numfmts
    )
    xf_tags = "".join(f'<xf numFmtId="{nid}"/>' for nid in cell_xfs)
    members["xl/styles.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{ns}">'
        f'<numFmts count="{len(numfmts)}">{fmt_tags}</numFmts>'
        f'<cellXfs count="{len(cell_xfs)}">{xf_tags}</cellXfs>'
        "</styleSheet>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _read(client: TestClient, path: str) -> dict:
    return client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": path}
    ).json()


# -- tests -----------------------------------------------------------------------


def test_sheet_preview_multi_sheet_values(tmp_path):
    make_xlsx(
        tmp_path / "book.xlsx",
        [
            (
                "Data",
                [
                    [("s", "Item"), ("s", "Qty")],
                    [("s", "Widget"), ("n", 3)],
                    [None, ("b", True)],  # gap cell + boolean
                ],
            ),
            ("Notes", [[("inline", "hello"), ("n", 2.5)]]),
        ],
    )

    body = _read(_client(tmp_path), "book.xlsx")
    assert body["ok"] is True
    assert body["kind"] == "sheet"
    assert body.get("data_url") is None  # binary no longer shipped to the renderer
    assert [s["name"] for s in body["sheets"]] == ["Data", "Notes"]

    data, notes = body["sheets"]
    assert data["truncated"] is False and data["total_rows"] == 3
    assert data["rows"] == [
        ["Item", "Qty"],
        ["Widget", 3],
        ["", "TRUE"],
    ]
    assert notes["rows"] == [["hello", 2.5]]


def test_sheet_preview_renders_dates_not_serial_numbers(tmp_path):
    """A numeric cell whose style is a date format shows a readable date, not the
    raw Excel serial (styles.xml is parsed for numFmt codes)."""
    _make_xlsx_with_styles(
        tmp_path / "dated.xlsx",
        numfmts=[(164, "yyyy-mm-dd")],
        cell_xfs=[0, 164],  # 0 = General, 1 (index 1) = custom date
        rows=[
            ("Sheet1", [
                [("inline", "Date"), ("inline", "Amount")],
                [("n", 45123, 1), ("n", 99.5, 0)],
            ]),
        ],
    )
    body = _read(_client(tmp_path), "dated.xlsx")
    assert body["ok"] is True
    rows = body["sheets"][0]["rows"]
    assert rows[0] == ["Date", "Amount"]
    assert rows[1][0] == "2023-07-16"  # 45123 → 2023-07-16 (epoch 1899-12-30 base)
    assert rows[1][1] == 99.5  # plain numeric (General style) unchanged


def test_sheet_preview_renders_datetime_and_time_formats(tmp_path):
    _make_xlsx_with_styles(
        tmp_path / "times.xlsx",
        numfmts=[(165, "h:mm:ss"), (166, "yyyy-mm-dd h:mm:ss")],
        cell_xfs=[0, 165, 166],
        rows=[
            ("Sheet1", [
                [("n", 0.5, 1), ("n", 45123.6770833333, 2)],
            ]),
        ],
    )
    body = _read(_client(tmp_path), "times.xlsx")
    row = body["sheets"][0]["rows"][0]
    # Time-only (no date tokens): '0.5' → 12:00:00.
    assert row[0] == "12:00:00"
    # Datetime: serial + fractional day → both date and time.
    assert row[1].startswith("2023-07-16 16:14:59")


def test_sheet_preview_does_not_render_plain_numbers_as_dates(tmp_path):
    _make_xlsx_with_styles(
        tmp_path / "nums.xlsx",
        numfmts=[(167, "General")],
        cell_xfs=[0, 167],
        rows=[
            ("Sheet1", [[("n", 0), ("n", 45000, 1)]]),
        ],
    )
    body = _read(_client(tmp_path), "nums.xlsx")
    # 'General' has no date tokens → the integer survives as an integer.
    assert body["sheets"][0]["rows"][0] == [0, 45000]


def test_sheet_preview_row_cap_reports_totals(tmp_path):
    rows = [[("n", i)] for i in range(600)]
    make_xlsx(tmp_path / "big.xlsx", [("Sheet1", rows)])

    body = _read(_client(tmp_path), "big.xlsx")
    sheet = body["sheets"][0]
    # 501 kept rows = header + the 500 body rows the UI table shows.
    assert len(sheet["rows"]) == 501
    assert sheet["total_rows"] == 600
    assert sheet["truncated"] is True
    assert sheet["rows"][500] == [500]


def test_sheet_preview_caps_columns_and_cell_text(tmp_path):
    wide_row = [("n", c) for c in range(200)]
    wide_row.append(("s", "x" * 5000))  # col 200: inside the grid, over the text cap
    wide_row += [("n", c) for c in range(201, 301)]  # beyond the column cap
    make_xlsx(tmp_path / "wide.xlsx", [("W", [wide_row])])

    body = _read(_client(tmp_path), "wide.xlsx")
    sheet = body["sheets"][0]
    assert len(sheet["rows"][0]) <= 256  # column cap keeps responses bounded
    assert all(not isinstance(c, str) or len(c) <= 1000 for c in sheet["rows"][0])


def test_sheet_preview_corrupt_file_degrades_gracefully(tmp_path):
    (tmp_path / "broken.xlsx").write_bytes(b"PK\x03\x04 definitely not a workbook")

    body = _read(_client(tmp_path), "broken.xlsx")
    assert body["ok"] is False
    assert body["kind"] == "sheet"
    assert "could not parse" in body["error"]
    assert body.get("sheets") is None


def test_sheet_preview_legacy_xls_gets_friendly_error(tmp_path):
    (tmp_path / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0 legacy BIFF")

    body = _read(_client(tmp_path), "old.xls")
    assert body["ok"] is False
    assert "Reveal" in body["error"]
