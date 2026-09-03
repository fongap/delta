"""ARCH-001 gate — the source ledger: capture, fingerprint versioning, citations, freshness."""

from __future__ import annotations

import asyncio
import hashlib

from core.sources import (
    KIND_PAGE,
    FRESH_CHANGED,
    FRESH_CURRENT,
    FRESH_MISSING,
    ORIGIN_FILE,
    CitationRange,
    SourceStore,
    to_dto,
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_capture_file_hashes_content(tmp_path):
    f = tmp_path / "report.csv"
    f.write_bytes(b"a,b\n1,2\n")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref = store.capture_file(f)
    assert ref.fingerprint == _sha(f)
    assert ref.location == "report.csv"
    assert ref.origin == ORIGIN_FILE
    assert ref.status == FRESH_CURRENT
    assert ref.checked_at is not None


def test_recapture_new_content_flips_old_to_changed(tmp_path):
    f = tmp_path / "report.csv"
    f.write_bytes(b"v1")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    first = store.capture_file(f)
    f.write_bytes(b"v2")
    second = store.capture_file(f)
    assert first.id != second.id and first.fingerprint != second.fingerprint
    assert store.get(first.id).status == FRESH_CHANGED
    assert store.get(second.id).status == FRESH_CURRENT


def test_recapture_identical_content_returns_same_ref(tmp_path):
    f = tmp_path / "report.csv"
    f.write_bytes(b"same")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    first = store.capture_file(f)
    again = store.capture_file(f)
    assert again.id == first.id
    assert len(store.list(location="report.csv")) == 1


def test_freshness_pass_detects_changed_and_missing(tmp_path):
    changed = tmp_path / "changed.txt"
    missing = tmp_path / "missing.txt"
    stable = tmp_path / "stable.txt"
    changed.write_bytes(b"old")
    missing.write_bytes(b"gone")
    stable.write_bytes(b"keep")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    for f in (changed, missing, stable):
        store.capture_file(f)
    changed.write_bytes(b"new")
    missing.unlink()
    drifted = store.check_freshness()
    statuses = {r.location: r.status for r in store.list()}
    assert statuses == {
        "changed.txt": FRESH_CHANGED,
        "missing.txt": FRESH_MISSING,
        "stable.txt": FRESH_CURRENT,
    }
    assert {r.location for r in drifted} == {"changed.txt", "missing.txt"}


def test_check_freshness_async_matches_sync(tmp_path):
    f = tmp_path / "data.csv"
    f.write_bytes(b"x")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref = store.capture_file(f)
    f.write_bytes(b"y")

    async def run():
        return await store.check_freshness_async()

    drifted = asyncio.run(run())
    assert [r.id for r in drifted] == [ref.id]
    assert store.get(ref.id).status == FRESH_CHANGED


def test_mark_cited_and_persistence(tmp_path):
    f = tmp_path / "book.pdf"
    f.write_bytes(b"pdf")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref = store.capture_file(f)
    assert (
        store.mark_cited(
            ref.id, "run-1", [CitationRange(kind=KIND_PAGE, page=2)]
        )
        is True
    )
    assert store.mark_cited("nope", "run-1", []) is False

    reloaded = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    got = reloaded.get(ref.id)
    assert got.cited_ranges == [
        {"run_id": "run-1", "ranges": [{"kind": "page", "page": 2}]}
    ]


def test_list_filters_and_latest(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
    ref_a = store.capture_file(a)
    store.capture_file(b)
    assert len(store.list()) == 2
    assert len(store.list(status=FRESH_CURRENT)) == 2
    assert len(store.list(origin=ORIGIN_FILE)) == 2
    assert store.latest("a.txt").id == ref_a.id
    assert store.latest("nope.txt") is None


def test_capture_outside_workspace_keeps_absolute_location(tmp_path):
    outside = tmp_path.parent / "outside-srcs-test.txt"
    outside.write_bytes(b"z")
    try:
        store = SourceStore(tmp_path / "sources.json", workspace=tmp_path)
        ref = store.capture_file(outside)
        assert ref.location == str(outside)
    finally:
        outside.unlink()


def test_source_dto_contract():
    from services.server.contracts import SourceDTO

    dto = to_dto(
        type(
            "Ref",
            (),
            {
                "id": "abc",
                "origin": "file",
                "location": "docs/report.csv",
                "fingerprint": "0123456789abcdef",
                "status": "current",
                "cited_ranges": [],
            },
        )()
    )
    assert dto["id"] == "abc"
    assert dto["origin"] == "file"
    assert dto["name"] == "report.csv"
    assert dto["fingerprint_prefix"] == "0123456789ab"
    assert dto["freshness"] == "current"
    assert dto["location"] == "docs/report.csv"
    assert dto["cited_ranges"] == []

    # The contract itself validates shape and defaults.
    parsed = SourceDTO(
        id="x", origin="url", name="Page", fingerprint_prefix="deadbeef"
    )
    assert parsed.freshness == "current"
    assert parsed.location is None
    assert parsed.cited_ranges == []
