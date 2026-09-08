"""Artifact Registry Hard-Cut tests (ADR-026).

The R2 Artifact Registry hard-cut makes Rust ``delta_core`` the sole
authority for artifact facts.  Python retains file discovery / stat /
sha256 / kind classification only.

These tests cover two surfaces:

1. **Architecture guard**: the delegate is deleted, the facade has no
   Python writer or authority switch, and production registers through
   the Rust facade.
2. **Behavior**: single register, missing file, hash failure, batch
   scan, duplicate registration, and fail-closed on Rust unavailability

Contract: ``docs/architecture/adr/ADR-026-r2-artifact-hard-cut.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.artifact import (
    _sha256_of,
    register_artifact,
    register_run_artifacts,
)
from core.ledger import RunEventLedger

REPO = Path(__file__).resolve().parent.parent

NO_IS_RUST_AUTHORITY = re.compile(r"is_rust_authority")
NO_DELTA_RUST_AUTHORITY = re.compile(r"DELTA_RUST_AUTHORITY")
NO_DELEGATE_IMPORT = re.compile(
    r"from\s+core\.artifact_delegate\s+import|\
import\s+core\.artifact_delegate"
)


# -- architecture guard -----------------------------------------------------


def test_legacy_artifact_delegate_is_deleted():
    """core/artifact_delegate.py must not exist after ADR-026 hard-cut."""
    assert not (REPO / "core" / "artifact_delegate.py").exists()


def test_legacy_artifact_delegate_test_deleted():
    """tests/test_artifact_delegate.py must not exist after ADR-026."""
    assert not (REPO / "tests" / "test_artifact_delegate.py").exists()


def test_artifact_facade_has_no_python_writer_or_switch():
    """core/artifact.py must be a thin discovery facade: no sqlite3
    writer, no is_rust_authority / DELTA_RUST_AUTHORITY reference, and
    it must route registration through DeltaCoreClient."""
    source = (REPO / "core" / "artifact.py").read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert not NO_IS_RUST_AUTHORITY.search(source)
    assert not NO_DELTA_RUST_AUTHORITY.search(source)
    assert "artifact.register" in source


def test_no_production_file_imports_deleted_artifact_delegate():
    """No production file should import the deleted artifact_delegate module."""
    violations: list[str] = []
    for search_dir in [REPO / "core", REPO / "services"]:
        if not search_dir.exists():
            continue
        for py in search_dir.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if NO_DELEGATE_IMPORT.search(text):
                violations.append(str(py.relative_to(REPO)))
    assert not violations, f"Files import deleted artifact_delegate: {violations}"


def test_artifact_not_in_rust_write_domains():
    """artifact must not be in RUST_WRITE_DOMAINS (hard-cut, ADR-026)."""
    from packages.storage_authority import RUST_WRITE_DOMAINS

    assert "artifact" not in RUST_WRITE_DOMAINS


# -- behavior: single registration ------------------------------------------


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run_events.db")
    yield inst
    inst.close()


def test_register_single_artifact_writes_two_events(tmp_path, ledger):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "out.md").write_text("hello world", encoding="utf-8")
    ledger.append("run_1", "run.started", actor="user")

    art = register_artifact(str(ws), "out.md", run_id="run_1", ledger=ledger)

    assert art is not None
    assert art.path == "out.md"
    assert art.name == "out.md"
    assert art.kind == "markdown"
    assert art.size == len("hello world")
    assert art.sha256 == _sha256_of(ws / "out.md")
    assert art.incomplete is False
    assert isinstance(art.registered_at, float)

    events = ledger.events("run_1")
    types = [e["type"] for e in events]
    assert types.count("artifact.registered") == 1
    assert types.count("artifact.completed") == 1
    reg = [e for e in events if e["type"] == "artifact.registered"][0]
    assert reg["payload"]["path"] == "out.md"
    assert reg["payload"]["sha256"] == art.sha256


def test_register_missing_file_returns_none(tmp_path, ledger):
    assert register_artifact(str(tmp_path), "nope.md", run_id="r1", ledger=ledger) is None


def test_register_hash_failure_marks_incomplete(tmp_path, ledger, monkeypatch):
    from core import artifact as mod

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "broken.md").write_text("data", encoding="utf-8")
    ledger.append("run_1", "run.started", actor="user")

    real = mod._sha256_of

    def flaky(path):
        if path.name == "broken.md":
            raise OSError("simulated truncation")
        return real(path)

    monkeypatch.setattr(mod, "_sha256_of", flaky)
    art = register_artifact(str(ws), "broken.md", run_id="run_1", ledger=ledger)
    assert art is not None
    assert art.incomplete is True
    assert art.sha256 is None

    events = ledger.events("run_1")
    types = [e["type"] for e in events]
    assert types.count("artifact.registered") == 1
    assert types.count("artifact.completed") == 0


def test_register_workspace_relative_path(tmp_path, ledger):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "r.md").write_text("x", encoding="utf-8")
    ledger.append("run_1", "run.started", actor="user")

    art = register_artifact(str(ws), "sub/r.md", run_id="run_1", ledger=ledger)
    assert art is not None
    assert art.path == "sub/r.md"


# -- behavior: batch scan ---------------------------------------------------


def test_register_run_artifacts_scan_and_register(tmp_path, ledger):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.md").write_text("a", encoding="utf-8")
    (ws / "b.csv").write_text("1,2", encoding="utf-8")
    (ws / ".hidden").mkdir()
    (ws / ".hidden" / "h.md").write_text("h", encoding="utf-8")
    ledger.append("run_1", "run.started", actor="user")

    arts = register_run_artifacts(str(ws), run_id="run_1", since=0.0, ledger=ledger)

    paths = {a.path for a in arts}
    assert paths == {"a.md", "b.csv"}

    events = ledger.events("run_1")
    types = [e["type"] for e in events]
    assert types.count("artifact.registered") == 2
    assert types.count("artifact.completed") == 2


def test_register_run_artifacts_missing_workspace():
    arts = register_run_artifacts(
        str(Path("/nonexistent/workspace")), run_id="r1", since=0.0
    )
    assert arts == []


# -- behavior: duplicate registration ---------------------------------------


def test_duplicate_registration_appends_again(tmp_path, ledger):
    """Re-registering the same (run_id, path, hash) appends a second
    pair of events (the current behavior: registration is append-only,
    not de-duplicated)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "out.md").write_text("dup", encoding="utf-8")
    ledger.append("run_1", "run.started", actor="user")

    register_artifact(str(ws), "out.md", run_id="run_1", ledger=ledger)
    register_artifact(str(ws), "out.md", run_id="run_1", ledger=ledger)

    events = ledger.events("run_1")
    types = [e["type"] for e in events]
    assert types.count("artifact.registered") == 2
    assert types.count("artifact.completed") == 2
    assert ledger.verify("run_1") is True


# -- behavior: fail-closed --------------------------------------------------


def test_register_fails_closed_when_binary_missing(tmp_path, monkeypatch):
    """When delta_core is unavailable, registration must raise
    DeltaCoreError and never silently fall back to a Python writer."""
    from packages.delta_core_client import DeltaCoreError
    from packages import delta_core_client

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "out.md").write_text("x", encoding="utf-8")

    ledger = RunEventLedger(tmp_path / "run_events.db")
    ledger.append("run_1", "run.started", actor="user")
    ledger.close()

    # Force binary lookup to fail and drop the cached client so the next
    # default_client() attempts a fresh spawn with no binary available.
    monkeypatch.setattr(delta_core_client, "_find_delta_core_binary", lambda: None)
    monkeypatch.setattr(delta_core_client, "_default_client", None)

    with pytest.raises(DeltaCoreError):
        register_artifact(str(ws), "out.md", run_id="run_1", ledger=ledger)