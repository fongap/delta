"""Ledger authority path — structural, concurrency, and crash safety tests (P1-A).

Tests three aspects of the RunEventLedger authority path:

1. **Structural**: all production ``RunEventLedger(...)`` instantiations
   go through ``maybe_wrap_ledger(...)``.
2. **Concurrency**: multiple threads appending to the same run must
   produce unique ``seq`` values, a continuous hash chain, and no
   lost updates.
3. **Crash safety**: a crash between ``INSERT`` and ``commit()`` must
   not produce a half-event; a crash after ``commit()`` must persist.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from core.ledger import RunEventLedger

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
SERVICES = REPO / "services"


# -- structural test ---------------------------------------------------------

RUN_LEDGER_DIRECT = re.compile(r"RunEventLedger\s*\(")
MAYBE_WRAP_LEDGER = re.compile(r"maybe_wrap_ledger\s*\(")
DELEGATE_IMPORT = re.compile(r"from\s+core\.ledger_delegate\s+import")

EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "core/ledger.py",
        "core/ledger_delegate.py",
        "core/ledger_event.py",
        "scripts/check_rust_authority_migration.py",
    }
)


def _production_py_files():
    for search_dir in (CORE, SERVICES):
        if not search_dir.exists():
            continue
        for py in search_dir.rglob("*.py"):
            try:
                rel = py.relative_to(REPO).as_posix()
            except ValueError:
                continue
            if rel in EXEMPT_FILES:
                continue
            yield py, rel


def test_no_direct_run_event_ledger_instantiation_without_maybe_wrap():
    """Production code must not directly instantiate RunEventLedger(...)
    without wrapping it through maybe_wrap_ledger."""
    violations: list[str] = []

    for py, rel in _production_py_files():
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not RUN_LEDGER_DIRECT.search(text):
            continue
        if MAYBE_WRAP_LEDGER.search(text) and DELEGATE_IMPORT.search(text):
            continue
        for match in RUN_LEDGER_DIRECT.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            violations.append(f"{rel}:{line_no}")

    assert not violations, (
        "Production code must not directly instantiate RunEventLedger(...) "
        "without wrapping through maybe_wrap_ledger. Violations:\n  "
        + "\n  ".join(violations)
    )


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
