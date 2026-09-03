"""P2 实用 — Source / Citation regression tests (DELTA_BLUEPRINT §7.2).

DELTA_BLUEPRINT §7.2 实用 lists two first-order requirements around Source:
  - 来源文件明确 (a citation must point at a concrete location)
  - 页码、段落、单元格、行号或其他可定位位置明确 (the locator must be specific)

These tests pin both ends:

  1. ``CitationRange`` is a typed schema with a ``kind`` discriminator. Each
     kind validates its own fields and serializes in canonical form so the UI
     can render the right pointer (line numbers, page numbers, cell ranges,
     message ids, or a connector-specific descriptor).
  2. ``SourceStore.mark_cited`` / ``add_citation`` reject malformed ranges
     *before* any mutation, so a run can never persist a citation the UI
     cannot render.
  3. ``SourceDTO`` exposes ``location`` and ``cited_ranges`` so the UI can
     show where a source lives and which runs / ranges have referenced it.
  4. ``InboxStore`` replies only accept the current ``[d:…]`` token — the
     P1 parse-compat with the OpenWorker rebrand aliases was terminated
     in P2, so a stray ``[ow:…]`` reply no longer resolves an item.
"""

from __future__ import annotations

import pytest

from core.inbox import InboxStore
from core.inbox_routing import resolve_from_reply
from core.sources import (
    KIND_CELLS,
    KIND_CUSTOM,
    KIND_LINES,
    KIND_MESSAGE_ID,
    KIND_PAGE,
    KIND_SHEET,
    CitationRange,
    SourceStore,
    normalize_cited_ranges,
    to_dto,
    to_range_dict,
)
from integrations.tools.files import file_tools


# -- 1. CitationRange: per-kind validation ------------------------------------


def test_lines_citation_accepts_partial_range():
    """A single-line citation only needs ``start``; ``end`` is optional."""
    out = to_range_dict(CitationRange(kind=KIND_LINES, start=42))
    assert out == {"kind": "lines", "start": 42}


def test_lines_citation_rejects_missing_range():
    with pytest.raises(ValueError, match="at least one of start/end"):
        to_range_dict(CitationRange(kind=KIND_LINES))


def test_lines_citation_rejects_non_int():
    with pytest.raises(ValueError, match="start must be int"):
        to_range_dict({"kind": KIND_LINES, "start": "12"})


def test_page_citation_accepts_single_page():
    out = to_range_dict(CitationRange(kind=KIND_PAGE, page=7))
    assert out == {"kind": "page", "page": 7}


def test_page_citation_accepts_page_range():
    out = to_range_dict(CitationRange(kind=KIND_PAGE, page=3, page_end=5))
    assert out == {"kind": "page", "page": 3, "page_end": 5}


def test_page_citation_rejects_missing_page():
    with pytest.raises(ValueError, match="at least one of page/page_end"):
        to_range_dict(CitationRange(kind=KIND_PAGE))


def test_cells_citation_accepts_full_axis_range():
    out = to_range_dict(
        CitationRange(
            kind=KIND_CELLS,
            sheet="2026-Q3",
            row_start=2,
            row_end=10,
            col_start=1,
            col_end=4,
        )
    )
    assert out == {
        "kind": "cells",
        "sheet": "2026-Q3",
        "row_start": 2,
        "row_end": 10,
        "col_start": 1,
        "col_end": 4,
    }


def test_cells_citation_accepts_a1_style_range():
    out = to_range_dict(
        CitationRange(kind=KIND_CELLS, sheet="Sheet1", cell_start="A2", cell_end="D10")
    )
    assert out == {
        "kind": "cells",
        "sheet": "Sheet1",
        "cell_start": "A2",
        "cell_end": "D10",
    }


def test_sheet_citation_requires_sheet_name():
    with pytest.raises(ValueError, match="'sheet' name"):
        to_range_dict(CitationRange(kind=KIND_SHEET, row_start=1, row_end=2))


def test_message_id_citation_requires_id():
    with pytest.raises(ValueError, match="'message_id'"):
        to_range_dict(CitationRange(kind=KIND_MESSAGE_ID))


def test_custom_citation_requires_descriptor():
    out = to_range_dict(
        CitationRange(
            kind=KIND_CUSTOM, descriptor={"channel": "slack", "ts": "1700000000.000100"}
        )
    )
    assert out == {
        "kind": "custom",
        "descriptor": {"channel": "slack", "ts": "1700000000.000100"},
    }


def test_custom_citation_rejects_missing_descriptor():
    with pytest.raises(ValueError, match="'descriptor' dict"):
        to_range_dict({"kind": KIND_CUSTOM})


# -- 2. Shape + dispatch ------------------------------------------------------


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="unknown citation kind"):
        to_range_dict({"kind": "scribble"})


def test_dict_input_requires_kind_string():
    with pytest.raises(ValueError, match="'kind' string"):
        to_range_dict({"page": 1})


def test_dict_input_does_not_require_citation_range_instance():
    out = to_range_dict({"kind": KIND_LINES, "start": 9, "end": 12})
    assert out == {"kind": "lines", "start": 9, "end": 12}


def test_non_dict_non_range_input_is_rejected():
    with pytest.raises(ValueError, match="CitationRange or dict"):
        to_range_dict("lines 1-3")


def test_canonical_form_strips_none_fields():
    out = to_range_dict(
        CitationRange(
            kind=KIND_LINES, start=1, end=2, page=99, sheet="x", message_id="y"
        )
    )
    assert out == {"kind": "lines", "start": 1, "end": 2}


def test_normalize_cited_ranges_handles_empty_input():
    assert normalize_cited_ranges([]) == []
    assert normalize_cited_ranges(None) == []


def test_normalize_cited_ranges_validates_each_entry():
    with pytest.raises(ValueError, match="unknown citation kind"):
        normalize_cited_ranges(
            [
                CitationRange(kind=KIND_LINES, start=1),
                {"kind": "wat"},
            ]
        )


# -- 3. mark_cited / add_citation: persistence + atomicity --------------------


def _store_with_one_file(tmp_path) -> tuple[SourceStore, str]:
    f = tmp_path / "report.csv"
    f.write_bytes(b"a,b\n1,2\n")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref = store.capture_file(f)
    return store, ref.id


def _reload(tmp_path, ref_id: str):
    """Reload the SourceStore and assert the ref is present.

    Tests use this as the precondition for asserting on a persisted ref —
    keeps the failure signal honest if the JSON file is empty.
    """
    reloaded = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref = reloaded.get(ref_id)
    assert ref is not None, f"ref {ref_id} not found after persistence"
    return ref


def test_mark_cited_persists_canonical_ranges(tmp_path):
    store, ref_id = _store_with_one_file(tmp_path)
    assert (
        store.mark_cited(
            ref_id,
            "run-1",
            [
                CitationRange(kind=KIND_LINES, start=1, end=5),
                CitationRange(kind=KIND_PAGE, page=2),
            ],
        )
        is True
    )
    reloaded = _reload(tmp_path, ref_id)
    assert reloaded.cited_ranges == [
        {
            "run_id": "run-1",
            "ranges": [
                {"kind": "lines", "start": 1, "end": 5},
                {"kind": "page", "page": 2},
            ],
        }
    ]


def test_mark_cited_unknown_ref_returns_false(tmp_path):
    store, _ = _store_with_one_file(tmp_path)
    assert store.mark_cited("missing", "run-1", []) is False


def test_mark_cited_rejects_malformed_range_without_mutating(tmp_path):
    """A bad range must raise *before* the lock / save so a half-applied
    citation can never land in the source ledger."""
    store, ref_id = _store_with_one_file(tmp_path)
    with pytest.raises(ValueError, match="unknown citation kind"):
        store.mark_cited(ref_id, "run-1", [{"kind": "scribble"}])
    # Persisted state is untouched.
    reloaded = _reload(tmp_path, ref_id)
    assert reloaded.cited_ranges == []


def test_add_citation_appends_one_range(tmp_path):
    store, ref_id = _store_with_one_file(tmp_path)
    assert (
        store.add_citation(
            ref_id, "run-1", CitationRange(kind=KIND_LINES, start=3, end=4)
        )
        is True
    )
    reloaded = _reload(tmp_path, ref_id)
    assert reloaded.cited_ranges == [
        {"run_id": "run-1", "ranges": [{"kind": "lines", "start": 3, "end": 4}]}
    ]


def test_add_citation_accepts_dict_shape(tmp_path):
    store, ref_id = _store_with_one_file(tmp_path)
    assert (
        store.add_citation(
            ref_id, "run-1", {"kind": KIND_MESSAGE_ID, "message_id": "m-001"}
        )
        is True
    )
    reloaded = _reload(tmp_path, ref_id)
    assert reloaded.cited_ranges == [
        {"run_id": "run-1", "ranges": [{"kind": "message_id", "message_id": "m-001"}]}
    ]


def test_repeated_citations_for_the_same_run_accumulate(tmp_path):
    """A run that pages through a long file emits a citation per window; the
    ledger keeps them all so the UI can show every page that was read."""
    store, ref_id = _store_with_one_file(tmp_path)
    for start, end in [(1, 50), (51, 100), (101, 150)]:
        store.add_citation(
            ref_id, "run-1", CitationRange(kind=KIND_LINES, start=start, end=end)
        )
    reloaded = _reload(tmp_path, ref_id)
    # Three separate citations, one per call (so the UI can show each
    # window the run actually read, not just the union of them).
    ranges = reloaded.cited_ranges
    assert len(ranges) == 3
    assert all(entry["run_id"] == "run-1" for entry in ranges)
    assert [r["start"] for entry in ranges for r in entry["ranges"]] == [
        1,
        51,
        101,
    ]


# -- 4. SourceDTO surfaces location + cited_ranges ---------------------------


def test_to_dto_includes_location_and_empty_cited_ranges(tmp_path):
    f = tmp_path / "data.csv"
    f.write_bytes(b"x")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref = store.capture_file(f)
    dto = to_dto(ref)
    assert dto["location"] == "data.csv"
    assert dto["cited_ranges"] == []


def test_to_dto_passes_through_existing_citations(tmp_path):
    store, ref_id = _store_with_one_file(tmp_path)
    store.add_citation(
        ref_id, "run-7", CitationRange(kind=KIND_LINES, start=10, end=20)
    )
    reloaded = _reload(tmp_path, ref_id)
    dto = to_dto(reloaded)
    assert dto["location"] == "report.csv"
    assert dto["cited_ranges"] == [
        {"run_id": "run-7", "ranges": [{"kind": "lines", "start": 10, "end": 20}]}
    ]


def test_source_dto_omits_location_optional_default():
    """``location`` is optional-additive so consumers that haven't been
    updated still validate (they just don't render the field)."""
    from services.server.contracts import SourceDTO

    parsed = SourceDTO(
        id="x", origin="file", name="data.csv", fingerprint_prefix="deadbeef"
    )
    assert parsed.location is None
    assert parsed.cited_ranges == []
    dumped = parsed.model_dump()
    assert "location" in dumped  # surfaced for the UI even when None
    assert "cited_ranges" in dumped


# -- 5. Inbox parse-compat termination ----------------------------------------


def test_inbox_d_token_resolves(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"approve [d:{item.id}]", store.resolve) is True
    resolved = store.get(item.id)
    assert resolved is not None
    assert resolved.resolution == "allow"


def test_inbox_legacy_ow_token_no_longer_resolves(tmp_path):
    """P2 terminates the [ow:…] parse-compat promised in relay-mode-removal.md.
    A reply with the legacy spelling must NOT silently resolve the item —
    callers see None and can route it as a free-text message instead."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"approve [ow:{item.id}]", store.resolve) is None
    pending = store.get(item.id)
    assert pending is not None
    assert pending.resolution is None
    assert pending.state == "pending"


def test_inbox_legacy_ocw_token_no_longer_resolves(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Deploy?", inbox="ops")
    assert resolve_from_reply(f"approve [ocw:{item.id}]", store.resolve) is None


# -- 6. read_file auto-cites a SourceStore on the closure ---------------------
# The simplest audit-trail hook for the read_file tool: when a SourceStore +
# run_id are passed, every successful read captures the file and appends a
# line-range citation so the UI can show which lines the run actually saw.
# Readers (PDF/XLSX) will reuse the same hook for their own locator kinds.


def test_read_file_auto_cites_lines_to_run(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("line one\nline two\nline three\nline four\n")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    read_file = file_tools(str(tmp_path), source_store=store, run_id="run-x")[0]

    out = read_file(path="log.txt", start_line=2, max_lines=2)

    assert "content" in out
    assert out["start_line"] == 2 and out["end_line"] == 3
    cited = [r for r in store.list() if r.location == "log.txt"]
    assert len(cited) == 1
    assert cited[0].cited_ranges == [
        {
            "run_id": "run-x",
            "ranges": [{"kind": KIND_LINES, "start": 2, "end": 3}],
        }
    ]


def test_read_file_without_source_store_still_works(tmp_path):
    """Auto-citation is opt-in: callers that pass no SourceStore get the
    pre-P2 read_file behavior unchanged (the schema, output, and error
    contract are identical)."""
    f = tmp_path / "log.txt"
    f.write_text("line one\nline two\n")
    read_file = file_tools(str(tmp_path))[0]
    out = read_file(path="log.txt")
    assert "content" in out
    assert out["start_line"] == 1 and out["end_line"] == 2


def test_read_file_does_not_cite_on_error(tmp_path):
    """A read that fails (path escapes workspace, file missing, etc.) must
    not create a phantom SourceRef or a citation pointing at content that
    was never read."""
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    read_file = file_tools(str(tmp_path), source_store=store, run_id="run-x")[0]

    out = read_file(path="missing.txt")
    assert "error" in out
    assert store.list() == []
