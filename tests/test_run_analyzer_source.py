"""P3 Run Analyzer — ``source_citation_hits`` join.

Joins ``SourceRef.cited_ranges`` with the ledger's ``tool.finished``
events for read tools. Tests cover:

- one hit per ``cited_ranges`` entry, ordered most-recent first
- ledger payload join for ``read_file`` / ``read_document``
- unknown ``source_id`` → empty
- missing source_store → ValueError
- source store on a different workspace is still accepted as long as
  the source_id matches; the analyzer does not need to cross
  workspaces to surface citations on one source
"""

from __future__ import annotations

from core.analyzer import Analyzer, source_citation_hits
from core.ledger import RunEventLedger
from core.sources import KIND_LINES, KIND_PAGE, SourceStore


def _build(tmp_path, *, with_ledger: bool = True):
    ws = tmp_path / "ws"
    ws.mkdir()
    # Create the file so capture_file has bytes to hash.
    (ws / "doc.pdf").write_bytes(b"fake pdf content for the test fixture")
    store = SourceStore(tmp_path / "sources.json", workspace=ws)
    ref = store.capture_file(ws / "doc.pdf", workspace=ws)
    # Three citations from three runs.
    store.add_citation(
        ref.id,
        "run-1",
        {"kind": KIND_LINES, "start": 1, "end": 5},
    )
    store.add_citation(
        ref.id,
        "run-2",
        {"kind": KIND_PAGE, "page": 7},
    )
    store.add_citation(
        ref.id,
        "run-3",
        {"kind": KIND_PAGE, "page": 12},
    )
    led = RunEventLedger(tmp_path / "events.db")
    if with_ledger:
        # Only run-2 has a matching tool.finished event; run-1 / run-3
        # get their citation carried without a ledger payload.
        led.append(
            "run-2",
            "tool.finished",
            actor="engine",
            payload={"tool": "read_document", "path": "doc.pdf", "page": 7},
        )
    return ws, store, ref, led


def test_returns_one_hit_per_citation(tmp_path):
    ws, store, ref, led = _build(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, source_store=store)
        hits = analyzer.source_citation_hits(ref.id)
    finally:
        led.close()
    assert len(hits) == 3


def test_ledger_payload_joins_only_for_read_tools(tmp_path):
    ws, store, ref, led = _build(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, source_store=store)
        hits = analyzer.source_citation_hits(ref.id)
    finally:
        led.close()
    by_run = {h.run_id: h for h in hits}
    # run-2's tool.finished lands in the join.
    assert by_run["run-2"].ledger_payload is not None
    assert by_run["run-2"].ledger_payload["tool"] == "read_document"
    # run-1 / run-3 had no tool.finished row; payload is None.
    assert by_run["run-1"].ledger_payload is None
    assert by_run["run-3"].ledger_payload is None


def test_hits_sorted_by_captured_at_descending(tmp_path):
    """Order is most-recent-captured first; captured_at is on the
    SourceRef, not the citation, so the sort key is stable across
    repeated calls."""
    ws, store, ref, led = _build(tmp_path)
    try:
        hits = Analyzer(
            workspace=str(ws), ledger=led, source_store=store
        ).source_citation_hits(ref.id)
    finally:
        led.close()
    # Same captured_at on all three; ties are allowed in any order.
    assert all(h.captured_at == ref.captured_at for h in hits)


def test_unknown_source_id_returns_empty(tmp_path):
    ws, store, _, led = _build(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, source_store=store)
        hits = analyzer.source_citation_hits("source-does-not-exist")
    finally:
        led.close()
    assert hits == []


def test_missing_source_store_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = RunEventLedger(tmp_path / "events.db")
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led)
        try:
            analyzer.source_citation_hits("any")
        except ValueError as exc:
            assert "source_store" in str(exc)
        else:
            raise AssertionError(
                "Analyzer.source_citation_hits should reject when no source_store bound"
            )
    finally:
        led.close()


def test_empty_source_id_rejected(tmp_path):
    ws, store, _, led = _build(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, source_store=store)
        try:
            analyzer.source_citation_hits("")
        except ValueError as exc:
            assert "source_id" in str(exc)
        else:
            raise AssertionError("empty source_id must be rejected")
    finally:
        led.close()


def test_source_citation_hits_module_wrapper_parity(tmp_path):
    ws, store, ref, led = _build(tmp_path)
    try:
        via_class = Analyzer(
            workspace=str(ws), ledger=led, source_store=store
        ).source_citation_hits(ref.id)
        via_module = source_citation_hits(
            workspace=str(ws), source_id=ref.id, source_store=store, ledger=led
        )
    finally:
        led.close()
    assert [h.to_dict() for h in via_class] == [h.to_dict() for h in via_module]
