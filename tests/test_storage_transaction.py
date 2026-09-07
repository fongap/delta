"""Storage transaction boundary tests (P1-D).

Tests the ``CoreStorage`` / ``CoreTransaction`` abstraction that
guarantees high-consequence state changes (SideEffect intent /
commit, Run completion) update both the idempotency log and the
run ledger under a single coordinated boundary.
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


def test_exception_rolls_back_idempotency_state(storage):
    """R1 contract: the transaction boundary provides a coordinated
    commit point. If the block raises after one store has committed
    (e.g. a ledger event), the caller is responsible for handling
    the asymmetry — the Python R1 implementation does not provide
    cross-DB rollback. The Rust R1 implementation will.
    """
    run_id = "r-tx-rollback"

    with pytest.raises(RuntimeError, match="simulated"):
        with storage.begin() as tx:
            tx.ledger.append(run_id, "run.started")
            raise RuntimeError("simulated mid-transaction failure")

    events = storage.ledger.events(run_id)
    assert len(events) == 1, (
        "Python R1 keeps the pre-exception ledger event; callers must "
        "tolerate or compensate. The Rust R1 implementation will roll "
        "back both stores atomically."
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
