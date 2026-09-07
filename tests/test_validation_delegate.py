"""R2.2 production authority routing for deterministic Validation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from core.artifact import Artifact
from core.validation import ValidationCriteria, run_validation
from packages.delta_core_client import DeltaCoreError


class _FakeClient:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.payloads: list[dict[str, Any]] = []

    def command(self, payload: dict[str, Any]) -> Any:
        self.payloads.append(payload)
        return self.result


def _passing_result() -> dict[str, Any]:
    return {
        "ok": True,
        "checks": [{"name": "artifact_count", "ok": True, "detail": "0 artifacts"}],
        "evidence": {"artifact_count": 0},
    }


def test_run_validation_routes_to_rust_authority(monkeypatch):
    import core.validation_delegate as delegate

    client = _FakeClient(_passing_result())
    monkeypatch.setattr(delegate, "default_client", lambda: client)
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")
    criteria = ValidationCriteria(min_artifacts=0)

    result = run_validation([], criteria, workspace="材料 空间")

    assert result.ok
    assert result.evidence == {"artifact_count": 0}
    assert client.payloads == [
        {
            "cmd": "validation.run",
            "artifacts": [],
            "criteria": criteria.to_dict(),
            "workspace": "材料 空间",
            "valid_citation_count": None,
        }
    ]


@pytest.mark.parametrize("response", [None, {}, {"ok": True, "checks": []}])
def test_run_validation_rejects_malformed_core_response(monkeypatch, response):
    import core.validation_delegate as delegate

    monkeypatch.setattr(delegate, "default_client", lambda: _FakeClient(response))
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")

    with pytest.raises(DeltaCoreError):
        run_validation([], ValidationCriteria(min_artifacts=0))


def test_run_validation_propagates_core_failure(monkeypatch):
    import core.validation_delegate as delegate

    def unavailable():
        raise DeltaCoreError("core unavailable")

    monkeypatch.setattr(delegate, "default_client", unavailable)
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")

    with pytest.raises(DeltaCoreError, match="core unavailable"):
        run_validation([], ValidationCriteria(min_artifacts=0))


def test_real_delta_core_validation_handles_quoted_csv_and_fails_closed(
    monkeypatch, tmp_path
):
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

    workspace = tmp_path / "验证 空间"
    workspace.mkdir()
    output = workspace / "研究 数据.csv"
    output.write_text('"name,full",value\nAda,1\n', encoding="utf-8")
    artifact = Artifact(
        path=output.name,
        name=output.name,
        kind="spreadsheet",
        size=output.stat().st_size,
        modified_at=output.stat().st_mtime,
        run_id="run-1",
    )
    criteria = ValidationCriteria(
        min_artifacts=1,
        csv_required_headers={output.name: ["name,full", "value"]},
        require_citations=True,
        min_valid_citations=1,
    )

    close_default_client()
    monkeypatch.setenv("DELTA_CORE_BINARY", str(binary))
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")
    try:
        result = run_validation([artifact], criteria, workspace=str(workspace))
    finally:
        close_default_client()

    assert not result.ok
    assert result.checks[-1].name == "min_valid_citations"
    assert result.checks[-1].detail == "valid citation count unavailable"
    assert result.evidence["valid_citation_count"] is None
