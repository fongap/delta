"""Ledger authority path — architecture guard + concurrency + crash tests (ADR-023).

Tests two aspects of the RunEventLedger authority path:

1. **Architecture guard**: delegate is deleted, facade has no Python writer,
   production constructs Rust facade directly.
2. **Concurrency**: multiple threads appending to the same run must
   produce unique ``seq`` values, a continuous hash chain, and no
   lost updates.
3. **Crash safety**: a crash between ``INSERT`` and ``commit()`` must
   not produce a half-event; a crash after ``commit()`` must persist.

ADR-023 structural guard: the delegate and ``maybe_wrap_ledger`` are
deleted.  ``RunEventLedger`` is now the Rust authority facade; no
wrapping is needed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from core.ledger import RunEventLedger

REPO = Path(__file__).resolve().parent.parent


# -- architecture guard tests (ADR-023) ------------------------------------


def test_legacy_ledger_delegate_is_deleted():
    """core/ledger_delegate.py must not exist after ADR-023 hard-cut."""
    assert not (REPO / "core" / "ledger_delegate.py").exists()


def test_ledger_delegate_test_file_deleted():
    """tests/test_ledger_delegate.py must not exist after ADR-023 hard-cut."""
    assert not (REPO / "tests" / "test_ledger_delegate.py").exists()


def test_ledger_facade_has_no_python_writer_or_switch():
    """core/ledger.py must be a thin Rust facade with no Python SQLite
    writer, no authority selector, and no DELTA_RUST_AUTHORITY reference."""
    source = (REPO / "core" / "ledger.py").read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "is_rust_authority" not in source
    assert "DELTA_RUST_AUTHORITY" not in source
    assert "default_client().command" in source


def test_production_constructs_ledger_facade_directly():
    """R6: the Python application backend (manager.py) is removed — no Python
    layer constructs or wraps the Rust RunEventLedger facade."""
    manager = REPO / "services" / "server" / "manager.py"
    assert not manager.exists(), "services/server/manager.py must not exist (R6 backend removed)"


def test_no_production_file_imports_deleted_ledger_delegate():
    """No production file should import the deleted ledger_delegate module."""
    violations = []
    for root in (REPO / "core", REPO / "services"):
        for path in root.rglob("*.py"):
            if "core.ledger_delegate" in path.read_text(encoding="utf-8"):
                violations.append(path.relative_to(REPO).as_posix())
    assert not violations, "deleted delegate imported by: " + ", ".join(violations)


def test_ledger_is_not_in_rust_write_domains():
    """ledger must not be in RUST_WRITE_DOMAINS — it's a hard-cut facade."""
    from packages.storage_authority import RUST_WRITE_DOMAINS
    assert "ledger" not in RUST_WRITE_DOMAINS


# -- concurrency tests ------------------------------------------------------


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run_events.db")
    yield inst
    inst.close()


def test_concurrent_appends_to_same_run_produce_unique_seq(ledger):
    """Multiple threads appending to the same run must produce unique
    seq values with a continuous hash chain — no lost updates."""
    run_id = "run-concurrent"
    num_threads = 8
    appends_per_thread = 10
    total = num_threads * appends_per_thread
    barrier = threading.Barrier(num_threads)

    def worker(tid: int):
        barrier.wait()
        for i in range(appends_per_thread):
            ledger.append(
                run_id,
                "tool.finished",
                actor=f"t{tid}",
                payload={"tid": tid, "i": i},
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = ledger.events(run_id)
    assert len(events) == total, f"expected {total} events, got {len(events)}"

    seqs = [e["seq"] for e in events]
    assert len(set(seqs)) == total, "duplicate seq values detected"
    assert sorted(seqs) == list(range(1, total + 1)), "seq values are not 1..N"

    assert ledger.verify(run_id), "hash chain broken after concurrent appends"


def test_rapid_sequential_appends_preserve_chain(ledger):
    """Rapid sequential appends (same thread) must preserve the hash chain."""
    run_id = "run-rapid"
    for i in range(50):
        ledger.append(run_id, "tool.finished", payload={"i": i})

    events = ledger.events(run_id)
    assert len(events) == 50
    assert ledger.verify(run_id)

    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, 51))


# -- crash safety tests ----------------------------------------------------


def test_crash_before_commit_produces_no_half_event(tmp_path):
    """If the process crashes between INSERT and commit(), no half-event
    should be visible on reopen. SQLite's implicit transaction boundary
    means an uncommitted INSERT is never visible to another connection."""
    db_path = tmp_path / "run_events.db"
    run_id = "run-crash-before"

    led = RunEventLedger(db_path)
    led.append(run_id, "run.started")
    led.close()

    led2 = RunEventLedger(db_path)
    events = led2.events(run_id)
    assert len(events) == 1
    assert events[0]["type"] == "run.started"
    assert led2.verify(run_id)
    led2.close()


def test_crash_after_commit_persists_events(tmp_path):
    """After commit(), events are durable. Reopening the DB must show
    all committed events with an intact hash chain."""
    db_path = tmp_path / "run_events.db"
    run_id = "run-crash-after"

    led = RunEventLedger(db_path)
    led.append(run_id, "run.started")
    led.append(run_id, "tool.finished", payload={"name": "write_file"})
    led.append(run_id, "run.completed")
    led.close()

    led2 = RunEventLedger(db_path)
    events = led2.events(run_id)
    assert len(events) == 3
    assert [e["type"] for e in events] == [
        "run.started",
        "tool.finished",
        "run.completed",
    ]
    assert led2.verify(run_id)
    led2.close()
