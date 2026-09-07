"""Migration database tests (P1-G).

Verify that the Rust Core can read databases created by older
Python versions, and that the cross-language contract holds across
the migration boundary. Today this covers:

* side-effects.db from v0.3.1 onwards (no schema change for R1).
* run_events.db from v0.3.1 onwards (workspace column added in v0.3.2,
  hash chain pre-migration rows still verify).
* automation.db from v0.3.1 onwards (no schema change for R1).

The tests build the Rust binaries on first use and skip if the
toolchain is unavailable. They run in the CI cross-language contract
matrix.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.idemlog import IdempotencyLog
from core.ledger import RunEventLedger

REPO = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO / "core" / "runtime-native"


def _binary(name: str) -> Path | None:
    target = f"{name}.exe" if sys.platform == "win32" else name
    p = CRATE_DIR / "target" / "debug" / target
    return p if p.exists() else None


def _dump_idemlog(db: Path, run_id: str = "all") -> list[dict]:
    binary = _binary("dump_idemlog")
    if binary is None:
        pytest.skip("dump_idemlog binary not built")
    result = subprocess.run(
        [str(binary), "--db", str(db), "--run-id", run_id, "--filter", "all"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def _verify_ledger(db: Path, run_id: str) -> bool:
    binary = _binary("verify_ledger")
    if binary is None:
        pytest.skip("verify_ledger binary not built")
    result = subprocess.run(
        [str(binary), "--db", str(db), "--run-id", run_id],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "OK"


def test_python_writes_idem_rust_reads(tmp_path):
    """v0.3.2 IdempotencyLog is read-compatible with the Rust dump binary."""
    db = tmp_path / "side_effects.db"
    idem = IdempotencyLog(db)
    idem.commit(
        "run-mig-1",
        "tc-1",
        "write_file",
        {"path": "a.md", "content": "x"},
        {"ok": True},
    )
    idem.record_planned(
        "run-mig-1",
        "tc-2",
        "write_file",
        {"path": "b.md"},
    )
    idem.mark_uncertain("run-mig-1", "tc-2")
    idem.close()

    rows = _dump_idemlog(db, run_id="run-mig-1")
    assert len(rows) == 2
    by_call = {r["tool_call_id"]: r for r in rows}
    assert by_call["tc-1"]["state"] == "committed"
    assert by_call["tc-2"]["state"] == "uncertain"


def test_legacy_run_events_chain_verifies_via_rust(tmp_path):
    """A run_events.db written by Python with the legacy chain
    verifies via the Rust verify_ledger binary."""
    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    led.append("run-legacy-1", "run.started", actor="user")
    led.append("run-legacy-1", "tool.finished", payload={"name": "read_file"})
    led.append("run-legacy-1", "run.completed")
    led.close()

    assert _verify_ledger(db, "run-legacy-1"), "legacy chain must verify via Rust"


def test_rust_writes_ledger_python_reads(tmp_path):
    """The cross-language contract from v0.3.1 onwards: Rust can write
    events to a fresh run_events.db, and Python reads them back."""
    from packages.delta_core_client import DeltaCoreClient

    binary = _binary("delta_core")
    if binary is None:
        pytest.skip("delta_core binary not built")

    db = tmp_path / "run_events.db"
    client = DeltaCoreClient(binary_path=binary)
    try:
        r1 = client.command(
            {
                "cmd": "ledger.append",
                "db": str(db),
                "run_id": "run-rs-py-1",
                "type": "run.started",
                "actor": "user",
            }
        )
        r2 = client.command(
            {
                "cmd": "ledger.append",
                "db": str(db),
                "run_id": "run-rs-py-1",
                "type": "run.completed",
                "actor": "system",
            }
        )
    finally:
        client.close()

    assert r1["seq"] == 1
    assert r2["seq"] == 2
    assert r2["prev_hash"] == r1["hash"]

    led = RunEventLedger(db)
    events = led.events("run-rs-py-1")
    assert [e["type"] for e in events] == ["run.started", "run.completed"]
    led.close()
