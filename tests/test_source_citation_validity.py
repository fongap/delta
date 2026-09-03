"""P3 §7.3 Source 完整能力 - per-citation 有效性检查 (ADR-006 续).

SourceStore.validate_citation answers the question: "if a UI scrolled
to the cited lines / page / cells right now, would it land on the
same content the run saw?"

Four invalid reasons:
- content_changed  - file sha256 differs (content changed; UI can
  still navigate, but the evidence is no longer what the run saw)
- out_of_bounds    - file is current, but the range is past EOF
  (truncated file, stale start_line)
- file_missing     - status: missing, file unreadable
- source_gone      - the ref itself was removed from the store
  (bookkeeping cleanup, must not be confused with file_missing)

Contract:
- Analyzer.source_citation_hits adds a `validity` field per hit
  (per-range checks rolled up to one (source, run) signal)
- Multi-range hits use worst-reason roll-up
  (file_missing > out_of_bounds > content_changed > valid)
- Non-lines kinds (page / cells / message_id / custom) have no cheap
  bound check; a current file means valid (the reader that opens it
  will error if the page is actually missing)
"""

from __future__ import annotations

from pathlib import Path

from core.analyzer import Analyzer
from core.ledger import RunEventLedger
from core.sources import (
    KIND_CELLS,
    KIND_LINES,
    KIND_MESSAGE_ID,
    KIND_PAGE,
    SourceStore,
)


# Reason constants live on SourceStore (P3 §7.3 Source 完整能力).
CITATION_VALID = SourceStore.CITATION_VALID
CITATION_CONTENT_CHANGED = SourceStore.CITATION_CONTENT_CHANGED
CITATION_OUT_OF_BOUNDS = SourceStore.CITATION_OUT_OF_BOUNDS
CITATION_FILE_MISSING = SourceStore.CITATION_FILE_MISSING
CITATION_SOURCE_GONE = SourceStore.CITATION_SOURCE_GONE


def _make_store_and_analyzer(tmp_path: Path):
    """Create a workspace + SourceStore + Analyzer with a sample file."""
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "doc.txt"
    f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    store = SourceStore(tmp_path / "sources.json", workspace=ws)
    analyzer = Analyzer(
        workspace=str(ws),
        ledger=RunEventLedger(tmp_path / "run.db"),
        source_store=store,
    )
    return ws, f, store, analyzer


# -- SourceStore.validate_citation direct ----------------------------------


def test_validate_citation_valid_for_unchanged_file_in_range(tmp_path):
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    out = store.validate_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 2}
    )
    assert out["valid"] is True
    assert out["status"] == "current"
    assert out["reason"] == CITATION_VALID
    assert out["current_line_count"] == 4


def test_validate_citation_out_of_bounds_when_file_truncated(tmp_path):
    """File is current (sha256 unchanged) but a line range past EOF
    is out_of_bounds - a stale windowed read after the file got
    shorter, or a citation that pointed past the last line."""
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    store.capture_file(f)
    # Truncate to 2 lines (sha256 changes, so a new ref is created).
    f.write_text("line1\nline2\n", encoding="utf-8")
    new_ref = store.capture_file(f)
    assert new_ref.fingerprint  # fresh ref exists
    # Now check a citation against the new ref that points past EOF.
    out = store.validate_citation(
        new_ref.id, "run-1", {"kind": KIND_LINES, "start": 5, "end": 6}
    )
    assert out["valid"] is False
    assert out["status"] == "current"
    assert out["reason"] == CITATION_OUT_OF_BOUNDS
    assert out["current_line_count"] == 2


def test_validate_citation_content_changed_marks_invalid(tmp_path):
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    # Mutate the file *without* re-capturing - simulates a file that
    # changed after the run was recorded.
    f.write_text("line1\nDIFFERENT\nline3\nline4\n", encoding="utf-8")
    out = store.validate_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 2}
    )
    assert out["valid"] is False
    assert out["status"] == "changed"
    assert out["reason"] == CITATION_CONTENT_CHANGED
    assert out["current_sha256"] != ref.fingerprint


def test_validate_citation_file_missing_when_unreadable(tmp_path):
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    f.unlink()
    out = store.validate_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 1}
    )
    assert out["valid"] is False
    assert out["status"] == "missing"
    assert out["reason"] == CITATION_FILE_MISSING


def test_validate_citation_file_missing_when_status_already_missing(tmp_path):
    """If the ref's status was already flipped to missing by a
    freshness pass, the validity check must not re-touch the
    filesystem (it should short-circuit on the status alone)."""
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    f.unlink()
    store.check_freshness()  # flips status to missing
    out = store.validate_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 1}
    )
    assert out["valid"] is False
    assert out["reason"] == CITATION_FILE_MISSING


def test_validate_citation_source_gone_when_ref_removed(tmp_path):
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    # The store exposes no explicit delete API; simulate a ref
    # removal by overwriting the in-memory refs.
    store._refs.clear()
    store._save()
    out = store.validate_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 1}
    )
    assert out["valid"] is False
    assert out["reason"] == CITATION_SOURCE_GONE
    assert out["status"] == "missing"


def test_validate_citation_non_lines_kind_validated_by_status_only(tmp_path):
    """page / cells / message_id / custom kinds don't have a cheap
    bound check - when the file is current we mark valid and let
    the reader that opens the file surface a real 'page not found'
    if the locator is off."""
    _ws, f, store, _ = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    for _kind, range_obj in [
        (KIND_PAGE, {"kind": KIND_PAGE, "page": 99}),
        (KIND_CELLS, {"kind": KIND_CELLS, "sheet": "Q3", "row_start": 1, "row_end": 3}),
        (KIND_MESSAGE_ID, {"kind": KIND_MESSAGE_ID, "message_id": "paragraph:12"}),
    ]:
        out = store.validate_citation(ref.id, "run-1", range_obj)
        assert out["valid"] is True
        assert out["reason"] == CITATION_VALID
        assert out["current_sha256"] == ref.fingerprint


# -- Analyzer.source_citation_hits: per-hit validity field ------------------


def test_hits_carry_validity_per_range(tmp_path):
    _ws, f, store, analyzer = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    store.add_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 2}
    )
    store.add_citation(
        ref.id, "run-2", {"kind": KIND_LINES, "start": 2, "end": 3}
    )
    hits = analyzer.source_citation_hits(ref.id)
    assert len(hits) == 2
    for h in hits:
        assert h.validity is not None
        assert h.validity["valid"] is True
        assert h.validity["reason"] == CITATION_VALID


def test_hit_validity_reflects_content_change(tmp_path):
    _ws, f, store, analyzer = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    store.add_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 2}
    )
    f.write_text("DIFFERENT\n", encoding="utf-8")
    hits = analyzer.source_citation_hits(ref.id)
    assert hits[0].validity["valid"] is False
    assert hits[0].validity["reason"] == CITATION_CONTENT_CHANGED


def test_hit_validity_reflects_file_missing(tmp_path):
    _ws, f, store, analyzer = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    store.add_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 1}
    )
    f.unlink()
    hits = analyzer.source_citation_hits(ref.id)
    assert hits[0].validity["reason"] == CITATION_FILE_MISSING


def test_hit_validity_rolls_up_to_worst_reason_for_multi_range(tmp_path):
    """One (source, run) hit can carry several ranges; the UI wants
    one signal per hit. The rollup picks the worst reason."""
    _ws, f, store, analyzer = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    # First range: valid (1..2 in a 4-line file).
    store.add_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 2}
    )
    # Second hit: multi-range with one valid + one out-of-bounds.
    store.mark_cited(
        ref.id,
        "run-1",
        [
            {"kind": KIND_LINES, "start": 1, "end": 2},
            {"kind": KIND_LINES, "start": 5, "end": 6},  # past EOF
        ],
    )
    hits = analyzer.source_citation_hits(ref.id)
    multi_range_hit = next(h for h in hits if len(h.citation["ranges"]) == 2)
    assert multi_range_hit.validity["valid"] is False
    assert multi_range_hit.validity["reason"] == CITATION_OUT_OF_BOUNDS


def test_to_dict_includes_validity(tmp_path):
    _ws, f, store, analyzer = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    store.add_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 1}
    )
    hits = analyzer.source_citation_hits(ref.id)
    d = hits[0].to_dict()
    assert "validity" in d
    assert d["validity"]["reason"] == CITATION_VALID


# -- module-level wrapper parity ------------------------------------------


def test_module_wrapper_passes_validity_through(tmp_path):
    from core.analyzer import source_citation_hits as module_fn

    ws, f, store, analyzer = _make_store_and_analyzer(tmp_path)
    ref = store.capture_file(f)
    store.add_citation(
        ref.id, "run-1", {"kind": KIND_LINES, "start": 1, "end": 1}
    )
    hits = module_fn(
        workspace=str(ws),
        source_id=ref.id,
        source_store=store,
        ledger=analyzer.ledger,
    )
    assert len(hits) == 1
    assert hits[0].validity is not None
    assert hits[0].validity["valid"] is True
