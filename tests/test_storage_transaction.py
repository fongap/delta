"""Storage coordination boundary tests (P1-D / P0-5 R1.5).

Tests the ``CoreStorage`` / ``CoreTransaction`` abstraction. R1.5
is explicit: this is a **coordinated lock-ordering boundary**,
not a true cross-DB atomic transaction. The two stores live in
two separate SQLite DB files; Python cannot provide cross-DB
rollback. The tests verify the lock-ordering guarantee and the
documented R1.5 semantics (no rollback on exception).
"""

from __future__ import annotations

import pytest

from core.idemlog import IdempotencyLog
from core.ledger import RunEventLedger
from core.storage_transaction import CoreStorage, CoreTransaction


@pytest.fixture
def idem(tmp_path) -> IdempotencyLog:
    inst = IdempotencyLog(tmp_path / "side_effects.db")
    yield inst
    inst.close()


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run_events.db")
    yield inst
    inst.close()


@pytest.fixture
def storage(idem, ledger) -> CoreStorage:
    return CoreStorage(idem, ledger)


def test_begin_returns_transaction_context(storage):
    """``begin()`` returns a context manager that acquires both locks."""
    with storage.begin() as tx:
        assert isinstance(tx, CoreTransaction)
        assert tx.idem is storage.idem
        assert tx.ledger is storage.ledger


def test_successful_exit_releases_locks(storage):
    """After the ``with`` block exits normally, both stores are unlocked."""
    with storage.begin():
        pass
    with storage.begin():
        pass


def test_ledger_append_inside_transaction_is_visible_after_exit(storage):
    """A ledger event appended inside the transaction is visible after
    the transaction commits."""
    run_id = "r-tx-1"
    with storage.begin() as tx:
        tx.ledger.append(run_id, "run.started")
        tx.ledger.append(run_id, "tool.finished", payload={"name": "read_file"})
    events = storage.ledger.events(run_id)
    assert len(events) == 2
    assert [e["type"] for e in events] == ["run.started", "tool.finished"]


def test_exception_does_not_roll_back_ledger_event(storage):
    """R1.5 contract: the Python boundary cannot roll back across two
    separate SQLite DB files. Whatever each store committed stays
    committed, even on exception. Callers that need compensation
    must do so explicitly. The Rust R1.5+ phase will provide a true
    cross-DB transaction.
    """
    run_id = "r-tx-rollback"

    with pytest.raises(RuntimeError, match="simulated"):
        with storage.begin() as tx:
            tx.ledger.append(run_id, "run.started")
            raise RuntimeError("simulated mid-transaction failure")

    events = storage.ledger.events(run_id)
    assert len(events) == 1, (
        "R1.5 keeps the pre-exception ledger event (Python has no "
        "cross-DB rollback). The Rust R1.5+ phase will provide a "
        "true cross-DB transaction."
    )


def test_high_consequence_side_effect_commit_in_one_transaction(storage):
    """The R1 contract: a successful side effect commits both the
    idempotency state AND the ledger event under one transaction.

    In the Python R1 implementation each store commits its own
    SQLite transaction; the ``CoreTransaction`` boundary is the
    coordinated lock + commit point that prevents the two stores
    from interleaving. The Rust R1 implementation will replace
    this with a single Rust transaction.
    """
    run_id = "r-tx-success"
    call_id = "call-success"

    with storage.begin() as tx:
        tx.idem.commit(
            run_id,
            call_id,
            "write_file",
            {"path": "out.md"},
            {"ok": True},
            ledger=tx.ledger,
        )

    row = storage.idem._row(run_id, call_id)
    assert row is not None
    assert row["state"] == "committed"
    events = storage.ledger.events(run_id)
    side_effect_events = [e for e in events if e["type"] == "side_effect.committed"]
    assert len(side_effect_events) == 1


def test_rollback_is_noop_in_r15(storage):
    """P0-5: CoreTransaction.rollback is a no-op in R1.5. Python
    cannot roll back across two separate SQLite DB files. The
    rollback method exists for API parity with the future
    Rust-backed implementation. Calling it must not raise and
    must not affect any committed state."""
    run_id = "r-tx-noop"
    with storage.begin() as tx:
        tx.ledger.append(run_id, "run.started")
        tx.idem.commit(
            run_id, "call-noop", "write_file", {"path": "x"}, {"ok": True},
            ledger=tx.ledger,
        )
        # Rollback is a no-op in R1.5; both stores keep what they wrote.
        tx.rollback()
        # After rollback, the committed state is still visible.
        row = storage.idem._row(run_id, "call-noop")
        assert row is not None
        assert row["state"] == "committed"
        events = storage.ledger.events(run_id)
        assert any(e["type"] == "run.started" for e in events)


def test_concurrent_transactions_are_serialized(storage):
    """Two transactions from different threads execute one at a
    time — the storage-level lock prevents interleaving. Either
    ordering (t1 then t2, or t2 then t1) is acceptable; what
    matters is that one finishes before the other starts."""
    import threading
    import time

    observed: list[str] = []
    barrier = threading.Barrier(2)

    def worker(name: str, run_id: str):
        barrier.wait(timeout=5)
        with storage.begin() as tx:
            observed.append(f"{name}:enter")
            time.sleep(0.05)
            tx.ledger.append(run_id, "run.started")
            observed.append(f"{name}:exit")

    t1 = threading.Thread(target=worker, args=("t1", "r1"))
    t2 = threading.Thread(target=worker, args=("t2", "r2"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(observed) == 4, f"expected 4 events, got {observed}"
    t1_enter = observed.index("t1:enter")
    t1_exit = observed.index("t1:exit")
    t2_enter = observed.index("t2:enter")
    t2_exit = observed.index("t2:exit")
    assert t1_enter < t1_exit
    assert t2_enter < t2_exit
    either_way_first = (t1_enter < t1_exit and t1_exit <= t2_enter and t2_enter < t2_exit)
    either_way_second = (t2_enter < t2_exit and t2_exit <= t1_enter and t1_enter < t1_exit)
    assert either_way_first or either_way_second, (
        f"transactions interleaved: {observed}"
    )
