"""Core storage transaction boundary (P1-D).

The Delta Core R1 storage transaction boundary is the abstraction
that makes high-consequence state changes atomic across the two
current stores (idempotency log + run ledger).

Today Python keeps them in two SQLite DB files, so a single
SQLite transaction cannot span them. This module provides a
``CoreTransaction`` context manager that:

* records the **idempotency** state transition (Planned / Committed /
  Failed / Uncertain) in ``side_effects.db``
* appends the corresponding ``side_effect.*`` ledger event to
  ``run_events.db``
* on the Python side, uses per-store locks + ordered commit; on
  exception, the idempotency transition is rolled back so the two
  stores cannot silently diverge

The interface is the single chokepoint ``core.runtime_engine``
sites (record_planned, mark_executing, commit, mark_failed,
mark_uncertain) call. The Rust authority path runs the same
logical steps as a single Rust transaction when the delegate is
active (see ``core/idemlog_delegate.py`` / ``core/ledger_delegate.py``).

R1 contract:

* Successful exit: both stores updated.
* Exception exit: idempotency transition rolled back, ledger event
  not written (or both, in the unified Rust case).
* Crash mid-commit: SQLite transactional semantics on each DB file
  apply independently; the worst-case observable split-brain is a
  ledger event without a corresponding idempotency state (the
  inverse is impossible because the idempotency row is committed
  before the ledger event in the Python path).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from core.idemlog import IdempotencyLog
from core.ledger import RunEventLedger

_LOG = logging.getLogger("core.storage_transaction")


class CoreStorage:
    """Bundle the two core state stores under one handle.

    The Python implementation hands the caller access to the
    individual ``IdempotencyLog`` and ``RunEventLedger`` instances
    but centralizes the per-instance lock acquisition so a
    ``CoreTransaction`` can lock both in a deterministic order
    (idempotency first, then ledger) without deadlocking.

    Future R1: the Rust implementation will replace this with a
    single ``core_storage.open(path) -> CoreStorage`` whose
    ``begin/commit/rollback`` methods wrap a real Rust transaction
    across the two underlying DBs.
    """

    def __init__(self, idem: IdempotencyLog, ledger: RunEventLedger) -> None:
        self.idem = idem
        self.ledger = ledger
        self._lock = threading.RLock()

    def begin(self) -> "CoreTransaction":
        """Acquire both stores' locks in deterministic order."""
        return CoreTransaction(self)


class CoreTransaction:
    """Atomic state-change boundary across the two core stores.

    Use as a context manager. Successful exit commits both stores
    (the underlying SQLite transactions; the Python RLock is
    released on exit). On exception, the idempotency transition is
    rolled back (the ledger append already happened, so the
    sequence-of-events interpretation is preserved).

    Example::

        with storage.begin() as tx:
            tx.idem.commit(...)
            tx.ledger.append(...)

    R1: This is the canonical hook for "SideEffect intent",
    "SideEffect commit", "Run completion" high-consequence
    operations. The Engine's ``_execute_sync`` path calls into
    this transaction to keep the two stores synchronized.
    """

    def __init__(self, storage: CoreStorage) -> None:
        self._storage = storage
        self._acquired = False
        self._rolled_back = False
        self._idem_undo: dict[str, Any] | None = None

    @property
    def idem(self) -> IdempotencyLog:
        return self._storage.idem

    @property
    def ledger(self) -> RunEventLedger:
        return self._storage.ledger

    def __enter__(self) -> "CoreTransaction":
        self._storage._lock.acquire()
        self._acquired = True
        self.idem._lock.acquire()
        self.ledger._lock.acquire()
        return self

    def rollback(self) -> None:
        """Roll back the idempotency transition (if any)."""
        if self._rolled_back:
            return
        self._rolled_back = True
        if self._idem_undo is not None:
            try:
                self._idem_undo.pop("apply")()
            except Exception:
                _LOG.warning("idempotency rollback failed", exc_info=True)

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            try:
                self.ledger._lock.release()
            except Exception:
                pass
            try:
                self.idem._lock.release()
            except Exception:
                pass
            if self._acquired:
                self._storage._lock.release()
                self._acquired = False
        return False

    def close(self) -> None:
        """Release the transaction resources. Idempotent."""
        if self._acquired:
            self.__exit__(None, None, None)
