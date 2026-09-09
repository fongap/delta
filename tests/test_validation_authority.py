"""Tests for ADR-028 Validation Hard-Cut: Rust authority path."""

from __future__ import annotations

import pytest

from core.artifact import Artifact
from core.validation import (
    ValidationCriteria,
    ValidationAuthorityError,
    run_validation,
    register_validation,
    get_validation,
    list_validations,
    latest_validation,
    evaluate_and_register,
)


def _mk(path: str, *, incomplete: bool = False, size: int = 100, run_id: str = "r1") -> Artifact:
    return Artifact(
        path=path,
        name=path.rsplit("/", 1)[-1],
        kind="text",
        size=size,
        modified_at=0.0,
        run_id=run_id,
        sha256=None if incomplete else "x" * 64,
        incomplete=incomplete,
    )


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary ledger DB path."""
    return str(tmp_path / "run_events.db")


def test_register_and_get_validation(db_path, tmp_path):
    """Register a validation criteria + result and retrieve it."""
    criteria = ValidationCriteria(min_artifacts=1, required_paths=["report.md"])
    from core.validation import ValidationResult, ValidationCheck
    result = ValidationResult(ok=True, checks=[ValidationCheck(name="artifact_count", ok=True, detail="1 artifacts")], evidence={"artifact_count": 1})
    evaluated_at = "2025-01-01T00:00:00Z"

    register_validation("run-1", criteria, result, evaluated_at, db_path=db_path, workspace=str(tmp_path))
    validations = list_validations(run_id="run-1", db_path=db_path)
    assert len(validations) == 1
    v = validations[0]
    assert v["run_id"] == "run-1"
    assert v["criteria"]["min_artifacts"] == 1
    assert v["result"]["ok"] is True

    # Get by ID
    fetched = get_validation(v["id"], db_path=db_path)
    assert fetched is not None
    assert fetched["id"] == v["id"]
    assert fetched["run_id"] == "run-1"


def test_list_validations_filter_by_run(db_path, tmp_path):
    """list_validations filters by run_id."""
    criteria = ValidationCriteria(min_artifacts=1)
    from core.validation import ValidationResult
    result = ValidationResult(ok=True, checks=[], evidence={})
    evaluated_at = "2025-01-01T00:00:00Z"

    register_validation("run-a", criteria, result, evaluated_at, db_path=db_path, workspace=str(tmp_path))
    register_validation("run-b", criteria, result, evaluated_at, db_path=db_path, workspace=str(tmp_path))

    all_v = list_validations(db_path=db_path)
    assert len(all_v) == 2

    run_a = list_validations(run_id="run-a", db_path=db_path)
    assert len(run_a) == 1
    assert run_a[0]["run_id"] == "run-a"

    run_b = list_validations(run_id="run-b", db_path=db_path)
    assert len(run_b) == 1
    assert run_b[0]["run_id"] == "run-b"


def test_latest_validation(db_path, tmp_path):
    """latest_validation returns the most recent by evaluated_at."""
    criteria = ValidationCriteria(min_artifacts=1)
    from core.validation import ValidationResult
    result = ValidationResult(ok=True, checks=[], evidence={})

    register_validation("run-1", criteria, result, "2025-01-01T00:00:00Z", db_path=db_path, workspace=str(tmp_path))
    register_validation("run-1", criteria, result, "2025-01-02T00:00:00Z", db_path=db_path, workspace=str(tmp_path))

    latest = latest_validation("run-1", db_path=db_path)
    assert latest is not None
    assert latest["evaluated_at"] == "2025-01-02T00:00:00Z"

    # Non-existent run returns None
    assert latest_validation("run-nonexistent", db_path=db_path) is None


def test_evaluate_and_register(db_path, tmp_path):
    """evaluate_and_register evaluates criteria against artifacts and persists."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report.md").write_text("# Report\nContent.")
    import os
    import time
    now = time.time()
    os.utime(ws / "report.md", (now, now))

    criteria = ValidationCriteria(min_artifacts=1, required_paths=["report.md"])
    artifacts = [_mk("report.md")]

    record, result = evaluate_and_register(
        "run-1",
        criteria,
        artifacts,
        db_path=db_path,
        workspace=str(ws),
    )

    assert result.ok is True
    assert record["run_id"] == "run-1"
    assert record["criteria"]["min_artifacts"] == 1
    assert "artifact_count" in record["result"]["evidence"]

    # Verify it's persisted
    validations = list_validations(run_id="run-1", db_path=db_path)
    assert len(validations) == 1


def test_run_validation_fail_closed_when_core_unavailable(monkeypatch):
    """run_validation fails closed when Rust core is unavailable."""
    from packages.delta_core_client import DeltaCoreError, DeltaCoreClient

    def unavailable_client(*args, **kwargs):
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(DeltaCoreClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(DeltaCoreClient, "command", unavailable_client)

    with pytest.raises(ValidationAuthorityError, match="Rust validation authority unavailable"):
        run_validation([], ValidationCriteria(min_artifacts=0))


def test_register_validation_fail_closed(monkeypatch, db_path, tmp_path):
    """register_validation fails closed when Rust core is unavailable."""
    from packages.delta_core_client import DeltaCoreError, DeltaCoreClient

    def unavailable_client(*args, **kwargs):
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(DeltaCoreClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(DeltaCoreClient, "command", unavailable_client)

    criteria = ValidationCriteria(min_artifacts=1)
    from core.validation import ValidationResult
    result = ValidationResult(ok=True, checks=[], evidence={})

    with pytest.raises(ValidationAuthorityError, match="Rust validation register failed"):
        register_validation("run-1", criteria, result, "2025-01-01T00:00:00Z", db_path=db_path, workspace=str(tmp_path))


def test_get_validation_fail_closed(monkeypatch, db_path):
    """get_validation fails closed when Rust core is unavailable."""
    from packages.delta_core_client import DeltaCoreError, DeltaCoreClient

    def unavailable_client(*args, **kwargs):
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(DeltaCoreClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(DeltaCoreClient, "command", unavailable_client)

    with pytest.raises(ValidationAuthorityError, match="Rust validation get failed"):
        get_validation("val-1", db_path=db_path)


def test_list_validations_fail_closed(monkeypatch, db_path):
    """list_validations fails closed when Rust core is unavailable."""
    from packages.delta_core_client import DeltaCoreError, DeltaCoreClient

    def unavailable_client(*args, **kwargs):
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(DeltaCoreClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(DeltaCoreClient, "command", unavailable_client)

    with pytest.raises(ValidationAuthorityError, match="Rust validation list failed"):
        list_validations(db_path=db_path)


def test_latest_validation_fail_closed(monkeypatch, db_path):
    """latest_validation fails closed when Rust core is unavailable."""
    from packages.delta_core_client import DeltaCoreError, DeltaCoreClient

    def unavailable_client(*args, **kwargs):
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(DeltaCoreClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(DeltaCoreClient, "command", unavailable_client)

    with pytest.raises(ValidationAuthorityError, match="Rust validation latest failed"):
        latest_validation("run-1", db_path=db_path)


def test_evaluate_and_register_fail_closed(monkeypatch, db_path, tmp_path):
    """evaluate_and_register fails closed when Rust core is unavailable."""
    from packages.delta_core_client import DeltaCoreError, DeltaCoreClient

    def unavailable_client(*args, **kwargs):
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(DeltaCoreClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(DeltaCoreClient, "command", unavailable_client)

    criteria = ValidationCriteria(min_artifacts=1)
    artifacts = [_mk("report.md")]

    with pytest.raises(ValidationAuthorityError, match="Rust validation eval failed"):
        evaluate_and_register("run-1", criteria, artifacts, db_path=db_path, workspace=str(tmp_path))