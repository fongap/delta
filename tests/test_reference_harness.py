"""Reference Task Harness — smoke gate.

Drives the three Reference Task classes through the REAL automation
runtime with a scripted provider. The provider stands in ONLY for the
model; read_document / write_file / ledger / idemlog / artifact /
validation are all real.

This is the "real-work verification" the blueprint calls for — not
synthetic unit tests, but an end-to-end run that produces and validates
a real artifact, with metrics recorded.
"""

from __future__ import annotations

import pytest

from core.reference_harness import (
    run_task_a,
    run_task_b,
    run_task_c,
)


@pytest.mark.asyncio
async def test_task_a_xlsx_report_validation(tmp_path):
    """XLSX -> read -> write report -> validate. Artifact exists, ledger
    narrative is complete, no uncertain side effects."""
    m = await run_task_a(tmp_path)
    assert m.success, m.notes
    assert not m.artifact_missing, m.notes
    assert not m.validation_failure, m.notes
    assert not m.side_effect_uncertain, m.notes


@pytest.mark.asyncio
async def test_task_b_pdf_evidence_report(tmp_path):
    """PDF -> read + cite -> write evidence report -> validate."""
    m = await run_task_b(tmp_path)
    assert m.success, m.notes
    assert not m.artifact_missing, m.notes
    assert not m.validation_failure, m.notes
    assert not m.side_effect_uncertain, m.notes


@pytest.mark.asyncio
async def test_task_c_automation_run_artifacts(tmp_path):
    """Automation -> run -> produce artifact. Recovery not needed on a
    clean run."""
    m = await run_task_c(tmp_path)
    assert m.success, m.notes
    assert not m.artifact_missing, m.notes
    assert not m.side_effect_uncertain, m.notes


@pytest.mark.asyncio
async def test_task_a_metrics_record_duration(tmp_path):
    """The harness records run duration for the task."""
    m = await run_task_a(tmp_path)
    assert m.run_duration_s > 0
    assert m.run_id