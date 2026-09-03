"""P2 实用 — read_document tool regression tests (DELTA_BLUEPRINT §7.2).

The tool covers the formats the blueprint lists first: PDF / XLSX / DOCX
(Markdown / TXT go through read_file). The auto-cite hook is the same
``cite(...)`` chokepoint that read_file uses; a successful read must
land a typed citation whose kind matches the source's locator
vocabulary (page for PDF, cells for XLSX, message_id for DOCX).

What the tests pin:

  1. Each format returns blocks; choosing a specific block by index
     returns that block's text + a citation of the right kind.
  2. The summary view (no block) returns block_count WITHOUT citing
     (we only cite the spot the run actually read).
  3. Bad inputs (path outside workspace, unknown extension, parse error)
     return an ``error`` payload and do NOT cite.
  4. The whole-reader works in a real engine path (e2e): the catalog
     exposes ``read_document``, the run reads a PDF, the source ledger
     records the citation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.automation.models import Schedule, ScheduledTask
from core.sources import KIND_CELLS, KIND_MESSAGE_ID, KIND_PAGE, SourceStore
from integrations.tools.documents import document_tools
from providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from services.server.manager import SessionManager


# -- fixtures: synthetic PDF / XLSX / DOCX ---------------------------------


def _write_pdf(target: Path, pages: int = 3) -> None:
    """Build a minimal multi-page PDF with extractable text per page."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    for i in range(pages):
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
    with open(target, "wb") as fh:
        writer.write(fh)


def _write_xlsx(target: Path) -> None:
    """Build a minimal valid .xlsx (one sheet, two rows, two columns)."""
    from openpyxl import Workbook  # type: ignore[import-not-found]

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Q3"
    ws.append(["region", "amount"])
    ws.append(["west", 100])
    ws.append(["east", 200])
    wb.save(str(target))


def _write_docx(target: Path, paragraphs: list[str]) -> None:
    """Build a minimal valid .docx with the given paragraphs."""
    from docx import Document  # type: ignore[import-not-found]

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(target))


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "report.pdf"
    _write_pdf(p, pages=3)
    return p


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    p = tmp_path / "data.xlsx"
    _write_xlsx(p)
    return p


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    p = tmp_path / "memo.docx"
    _write_docx(p, ["Hello world.", "Second paragraph.", "Third."])
    return p


def _store_and_reader(tmp_path: Path, *, run_id: str | None = "run-1"):
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    tools = document_tools(str(tmp_path), source_store=store, run_id=run_id)
    return store, tools[0]


# -- 1. PDF ------------------------------------------------------------------


def test_pdf_summary_does_not_cite(tmp_path, sample_pdf):
    store, read = _store_and_reader(tmp_path)
    out = read(path="report.pdf")
    assert out["kind"] == "pdf"
    assert out["block_count"] == 3
    assert len(out["blocks"]) == 3
    assert store.list() == []


def test_pdf_specific_page_cites_page_kind(tmp_path, sample_pdf):
    store, read = _store_and_reader(tmp_path)
    out = read(path="report.pdf", block=1)
    assert out["kind"] == "pdf"
    assert out["block"]["page"] == 2
    refs = store.list()
    assert len(refs) == 1
    assert refs[0].cited_ranges == [
        {
            "run_id": "run-1",
            "ranges": [{"kind": KIND_PAGE, "page": 2}],
        }
    ]


# -- 2. XLSX -----------------------------------------------------------------


def test_xlsx_summary_does_not_cite(tmp_path, sample_xlsx):
    store, read = _store_and_reader(tmp_path)
    out = read(path="data.xlsx")
    assert out["kind"] == "xlsx"
    assert out["block_count"] == 1
    labels = [b["label"] for b in out["blocks"]]
    assert labels and "Q3" in labels[0]
    assert store.list() == []


def test_xlsx_specific_sheet_cites_cells_kind(tmp_path, sample_xlsx):
    store, read = _store_and_reader(tmp_path)
    out = read(path="data.xlsx", block=0)
    assert out["kind"] == "xlsx"
    assert out["block"]["sheet"] == "Q3"
    refs = store.list()
    assert len(refs) == 1
    cited = refs[0].cited_ranges[0]["ranges"][0]
    assert cited["kind"] == KIND_CELLS
    assert cited["sheet"] == "Q3"
    assert cited["row_end"] >= 3  # header + 2 data rows


# -- 3. DOCX -----------------------------------------------------------------


def test_docx_summary_does_not_cite(tmp_path, sample_docx):
    store, read = _store_and_reader(tmp_path)
    out = read(path="memo.docx")
    assert out["kind"] == "docx"
    assert out["block_count"] == 3
    assert store.list() == []


def test_docx_specific_paragraph_cites_message_id(tmp_path, sample_docx):
    store, read = _store_and_reader(tmp_path)
    out = read(path="memo.docx", block=1)
    assert out["block"]["paragraph"] == 2
    assert "Second paragraph" in out["block"]["text"]
    refs = store.list()
    assert len(refs) == 1
    cited = refs[0].cited_ranges[0]["ranges"][0]
    assert cited["kind"] == KIND_MESSAGE_ID
    assert cited["message_id"] == "paragraph:2"


# -- 4. Error paths: a failed read must NOT leave a phantom citation --------


def test_path_outside_workspace_does_not_cite(tmp_path, sample_pdf):
    store, read = _store_and_reader(tmp_path)
    out = read(path="../outside.pdf")
    assert "error" in out
    assert store.list() == []


def test_unknown_extension_does_not_cite(tmp_path):
    # A file exists but with an extension read_document doesn't recognize
    # (TXT is the canonical "use read_file" case).
    (tmp_path / "notes.txt").write_text("hello")
    store, read = _store_and_reader(tmp_path)
    out = read(path="notes.txt")
    assert "error" in out
    assert "read_file" in out["error"]
    assert store.list() == []


def test_missing_file_does_not_cite(tmp_path):
    store, read = _store_and_reader(tmp_path)
    out = read(path="absent.pdf")
    assert "error" in out
    assert store.list() == []


def test_corrupt_pdf_does_not_cite(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    store, read = _store_and_reader(tmp_path)
    out = read(path="broken.pdf")
    assert "error" in out or out.get("block", {}).get("error") is True
    assert store.list() == []


# -- 5. No source_store: reader still works, no citation emitted ------------


def test_reader_works_without_source_store(tmp_path, sample_pdf):
    """The cite hook is opt-in. Callers that pass no SourceStore get the
    raw read behavior unchanged (matches the read_file convention)."""
    tools = document_tools(str(tmp_path))
    read = tools[0]
    out = read(path="report.pdf", block=0)
    assert out["kind"] == "pdf"
    assert out["block"]["page"] == 1


# -- 6. e2e: read_document through the real engine path ---------------------


class _ReadDocumentProvider(ProviderClient):
    """One-turn scripted provider: read_document once, then a final reply."""

    def __init__(self, args: dict):
        self._args = args
        self._consumed = False

    def complete(self, **kwargs):
        if not self._consumed:
            self._consumed = True
            return AssistantTurn(
                tool_calls=[
                    ToolCall(id="c_doc", name="read_document", arguments=self._args)
                ]
            )
        return AssistantTurn(text="read it", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _task_with_pdf(tmp_path) -> ScheduledTask:
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_pdf(ws / "report.pdf", pages=2)
    return ScheduledTask(
        title="read pdf",
        instructions="read the report",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        # ``code`` is the agent whose ``code_files`` capability wires
        # both read_file AND read_document with the auto-cite hook.
        agent="code",
    )


async def test_read_document_in_automation_run_auto_cites(tmp_path):
    task = _task_with_pdf(tmp_path)
    mgr = SessionManager(
        data_dir=tmp_path / "data",
        provider=_ReadDocumentProvider(args={"path": "report.pdf", "block": 1}),
    )
    mgr.task_store.save(task)

    run = await mgr._run_scheduled_task(task, trigger="schedule")

    store = mgr.source_store_for(task.workspace, run_id=None)  # type: ignore[attr-defined]
    assert store is not None
    refs = store.list()
    assert len(refs) == 1
    ref = refs[0]
    assert ref.location == "report.pdf"
    # G1 single identity: the citation joins the TaskRun's run id.
    assert ref.cited_ranges == [
        {
            "run_id": run.run_id,
            "ranges": [{"kind": KIND_PAGE, "page": 2}],
        }
    ]
    await mgr.aclose()
