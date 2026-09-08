"""R2.1 production authority routing for citation validity."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from core.analyzer import Analyzer
from core.citation import CitationCaptureError, cite
from core.ledger import RunEventLedger
from core.source_citation_delegate import validate_citation_delegated
from core.sources import KIND_LINES, SourceStore
from packages.delta_core_client import DeltaCoreError


def _verdict(*, valid: bool = True) -> dict[str, Any]:
    return {
        "validity": "valid" if valid else "range_invalid",
        "valid": valid,
        "status": "current",
        "reason": "valid" if valid else "out_of_bounds",
        "source_exists": True,
        "source_unchanged": True,
        "structure_valid": True,
        "revision_matches": True,
        "range_valid": valid,
    }


class _FakeClient:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.payloads: list[dict[str, Any]] = []

    def command(self, payload: dict[str, Any]) -> Any:
        self.payloads.append(payload)
        return self.result


def test_delegate_sends_source_range_and_workspace(monkeypatch, tmp_path):
    import core.source_citation_delegate as delegate

    client = _FakeClient(_verdict())
    monkeypatch.setattr(delegate, "default_client", lambda: client)
    source = {"id": "s1", "location": "研究 数据.txt", "fingerprint": "abc"}
    range_obj = {"kind": KIND_LINES, "start": 1}

    result = validate_citation_delegated(
        source, range_obj, workspace=tmp_path / "材料 空间"
    )

    assert result["validity"] == "valid"
    assert client.payloads == [
        {
            "cmd": "citation.validate",
            "source": source,
            "range": range_obj,
            "workspace": str(tmp_path / "材料 空间"),
        }
    ]


def test_store_canonicalizes_with_rust_before_mutation(monkeypatch, tmp_path):
    import core.source_citation_delegate as delegate

    workspace = tmp_path / "ws"
    workspace.mkdir()
    path = workspace / "doc.txt"
    path.write_text("one\n", encoding="utf-8")
    store = SourceStore(tmp_path / "sources.json", workspace=workspace)
    ref = store.capture_file(path)
    canonical = [{"kind": KIND_LINES, "start": 1}]
    client = _FakeClient(canonical)
    monkeypatch.setattr(delegate, "default_client", lambda: client)
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation")

    assert store.mark_cited(ref.id, "run-1", canonical)

    assert client.payloads == [
        {"cmd": "citation.canonicalize", "ranges": canonical}
    ]
    assert ref.cited_ranges[0]["ranges"] == canonical


def test_cite_fails_closed_on_capture_error_in_authority_mode(monkeypatch, tmp_path):
    class BrokenStore:
        def capture_file(self, *_args, **_kwargs):
            raise OSError("capture failed")

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation")

    with pytest.raises(CitationCaptureError, match="source capture failed"):
        cite(
            BrokenStore(),  # type: ignore[arg-type]
            "run-1",
            tmp_path / "missing.txt",
            {"kind": KIND_LINES, "start": 1},
        )


@pytest.mark.parametrize("result", [None, {}, {"validity": "valid"}])
def test_delegate_rejects_malformed_protocol_result(monkeypatch, result):
    import core.source_citation_delegate as delegate

    monkeypatch.setattr(delegate, "default_client", lambda: _FakeClient(result))
    with pytest.raises(DeltaCoreError):
        validate_citation_delegated(
            None, {"kind": KIND_LINES, "start": 1}, workspace=None
        )


def test_source_store_uses_rust_verdict_without_python_fallback(monkeypatch, tmp_path):
    import core.source_citation_delegate as delegate

    workspace = tmp_path / "材料 空间"
    workspace.mkdir()
    source_path = workspace / "研究 数据.txt"
    source_path.write_text("alpha\nbeta\n", encoding="utf-8")
    store = SourceStore(tmp_path / "sources.json", workspace=workspace)
    ref = store.capture_file(source_path)
    store.add_citation(ref.id, "run-1", {"kind": KIND_LINES, "start": 1})

    client = _FakeClient(_verdict())
    monkeypatch.setattr(delegate, "default_client", lambda: client)
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation")

    analyzer = Analyzer(
        workspace=str(workspace),
        ledger=RunEventLedger(tmp_path / "run.db"),
        source_store=store,
    )
    hits = analyzer.source_citation_hits(ref.id)

    assert hits[0].validity is not None
    assert hits[0].validity["validity"] == "valid"
    assert client.payloads[0]["cmd"] == "citation.validate"
    assert client.payloads[0]["source"]["id"] == ref.id


def test_source_store_propagates_core_failure(monkeypatch, tmp_path):
    import core.source_citation_delegate as delegate

    workspace = tmp_path / "ws"
    workspace.mkdir()
    source_path = workspace / "doc.txt"
    source_path.write_text("one\n", encoding="utf-8")
    store = SourceStore(tmp_path / "sources.json", workspace=workspace)
    ref = store.capture_file(source_path)

    def unavailable():
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(delegate, "default_client", unavailable)
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation")

    with pytest.raises(DeltaCoreError, match="core unavailable"):
        store.validate_citation(ref.id, "run-1", {"kind": KIND_LINES, "start": 1})


def test_analyzer_reaches_real_delta_core_authority(monkeypatch, tmp_path):
    """Production consumer -> SourceStore -> persistent client -> Rust verdict."""
    from packages.delta_core_client import close_default_client

    suffix = ".exe" if sys.platform == "win32" else ""
    binary = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "runtime-native"
        / "target"
        / "debug"
        / f"delta_core{suffix}"
    )
    if not binary.is_file():
        pytest.skip("delta_core debug binary is not built")

    workspace = tmp_path / "材料 空间"
    workspace.mkdir()
    source_path = workspace / "研究 数据.txt"
    source_path.write_text("alpha\nbeta\n", encoding="utf-8")
    store = SourceStore(tmp_path / "sources.json", workspace=workspace)
    ref = store.capture_file(source_path)
    analyzer = Analyzer(
        workspace=str(workspace),
        ledger=RunEventLedger(tmp_path / "run.db"),
        source_store=store,
    )

    close_default_client()
    monkeypatch.setenv("DELTA_CORE_BINARY", str(binary))
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation")
    try:
        store.add_citation(ref.id, "run-real", {"kind": KIND_LINES, "start": 2})
        hits = analyzer.source_citation_hits(ref.id)
    finally:
        close_default_client()

    assert hits[0].validity is not None
    assert hits[0].validity["validity"] == "valid"
    assert hits[0].validity["current_line_count"] == 2
