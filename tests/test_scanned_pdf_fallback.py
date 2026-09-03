"""P2 follow-up B: scanned PDF image fallback (ADR-006 续).

When ``read_document`` encounters a scanned PDF (pages where pypdf's
``extract_text`` returns ``""``), the page is rasterized via
``pdf_support.rasterize_file`` and the PNG data URL is included in the
block's ``image`` field.

Contract:

- A scanned page (no text) gets ``scanned: True`` and an ``image`` data URL.
- A text-bearing page stays unchanged (no ``scanned`` field, no ``image``).
- A mixed PDF (some text pages + some scanned) only rasterizes the empty pages.
- The cite hook still records the page number for scanned pages.
- When ``pypdfium2`` is unavailable, scanned pages get empty text and no
  ``image`` field (graceful degradation).
"""

from __future__ import annotations

from pathlib import Path


from core.sources import KIND_PAGE, SourceStore
from integrations.tools.documents import document_tools


def _write_scanned_pdf(target: Path, pages: int = 3) -> None:
    """Build a PDF with blank pages (no text content at all).
    These pages have no embedded text, so pypdf's ``extract_text``
    returns ``""`` — mirroring a real scanned PDF.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    with open(target, "wb") as fh:
        writer.write(fh)


def _write_mixed_pdf(target: Path) -> None:
    """Build a PDF where page 1 has text, page 2 is blank (scanned), page 3 has text."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    for i, has_text in enumerate([True, False, True]):
        page = writer.add_blank_page(width=300, height=400)
        if has_text:
            text_data = f"Page {i + 1} content"
            font_dict = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            content = DecodedStreamObject()
            content.set_data(
                f"BT /F1 12 Tf 50 {400 - 30 * (i + 1)} Td ({text_data}) Tj ET".encode()
            )
            page[NameObject("/Contents")] = content
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})}
            )
    with open(target, "wb") as fh:
        writer.write(fh)


def _store_and_reader(tmp_path: Path, *, run_id: str | None = "run-scan"):
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    tools = document_tools(str(tmp_path), source_store=store, run_id=run_id)
    return store, tools[0]


# -- scanned PDF: all pages are images -------------------------------------


def test_scanned_pdf_pages_get_image_and_scanned_flag(tmp_path):
    _write_scanned_pdf(tmp_path / "scan.pdf", pages=3)
    _store, read = _store_and_reader(tmp_path)
    out = read(path="scan.pdf", block=0)
    block = out["block"]
    assert block.get("scanned") is True
    assert "image" in block
    assert block["image"].startswith("data:image/png;base64,")
    assert "no extractable text" in block["text"]


def test_scanned_pdf_summary_marks_scanned_pages(tmp_path):
    _write_scanned_pdf(tmp_path / "scan.pdf", pages=2)
    _store, read = _store_and_reader(tmp_path)
    out = read(path="scan.pdf")  # summary
    for b in out["blocks"]:
        assert b.get("scanned") is True


def test_scanned_pdf_cite_still_records_page(tmp_path):
    _write_scanned_pdf(tmp_path / "scan.pdf", pages=3)
    store, read = _store_and_reader(tmp_path)
    read(path="scan.pdf", block=1)
    refs = store.list()
    assert len(refs) == 1
    cited = refs[0].cited_ranges[0]["ranges"][0]
    assert cited["kind"] == KIND_PAGE
    assert cited["page"] == 2


# -- mixed PDF: only scanned pages get image --------------------------------


def test_mixed_pdf_only_scanned_pages_get_image(tmp_path):
    _write_mixed_pdf(tmp_path / "mixed.pdf")
    _store, read = _store_and_reader(tmp_path)
    # Read each block individually to inspect text + image
    out0 = read(path="mixed.pdf", block=0)
    out1 = read(path="mixed.pdf", block=1)
    out2 = read(path="mixed.pdf", block=2)
    # Page 1 has text -- no scanned flag, no image
    assert not out0["block"].get("scanned")
    assert "image" not in out0["block"]
    assert "Page 1 content" in out0["block"]["text"]
    # Page 2 is blank -- scanned flag + image
    assert out1["block"].get("scanned") is True
    assert "image" in out1["block"]
    assert "no extractable text" in out1["block"]["text"]
    # Page 3 has text -- no scanned flag, no image
    assert not out2["block"].get("scanned")
    assert "image" not in out2["block"]
    assert "Page 3 content" in out2["block"]["text"]


def test_mixed_pdf_reading_scanned_page_cites_page(tmp_path):
    _write_mixed_pdf(tmp_path / "mixed.pdf")
    store, read = _store_and_reader(tmp_path)
    out = read(path="mixed.pdf", block=1)
    assert out["block"].get("scanned") is True
    refs = store.list()
    assert len(refs) == 1
    cited = refs[0].cited_ranges[0]["ranges"][0]
    assert cited["kind"] == KIND_PAGE
    assert cited["page"] == 2


# -- pure text PDF: no change at all ----------------------------------------


def test_text_pdf_no_scanned_flag_or_image(tmp_path):
    """Build a PDF with extractable text and confirm no scanned-page
    markers appear (regression for over-eager rasterization)."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    for i in range(2):
        page = writer.add_blank_page(width=300, height=400)
        text_data = f"Page {i + 1} content"
        font_dict = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        content = DecodedStreamObject()
        content.set_data(
            f"BT /F1 12 Tf 50 {400 - 30 * (i + 1)} Td ({text_data}) Tj ET".encode()
        )
        page[NameObject("/Contents")] = content
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})}
        )
    out_path = tmp_path / "text.pdf"
    with open(out_path, "wb") as fh:
        writer.write(fh)

    _store, read = _store_and_reader(tmp_path)
    out = read(path="text.pdf")
    for b in out["blocks"]:
        assert not b.get("scanned")
        assert "image" not in b
