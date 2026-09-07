"""Core storage coordination boundary (P1-D / P0-5 R1.5).

This module provides ``CoreStorage`` and ``CoreTransaction``, a
**coordinated lock-ordering boundary** for the two core state
stores (idempotency log + run ledger). It is **not** a true
cross-DB atomic transaction.

R1.5 reality (P0-5):

* The Python implementation keeps idempotency and ledger in two
  **separate SQLite DB files** (``side_effects.db`` and
  ``run_events.db``). A single SQLite transaction cannot span
  them. The two stores commit independently.
* This module centralizes the lock acquisition order
  (``idem._lock`` first, then ``ledger._lock``) so concurrent
  writers cannot deadlock. That is the entire guarantee it
  provides in Python.
* On exception inside the ``with`` block, the boundary does
  **not** roll back either store. Each store keeps whatever it
  committed. A ledger event may be visible without its
  corresponding idempotency state (or vice versa) if the
  process crashes between the two commits.
* A true cross-DB atomic transaction requires the unified Rust
  process (``delta_core``) to wrap both stores in a single
  Rust transaction. That is deferred to a later R1.5+ phase;
  see ADR-017 and ``docs/governance/rust-core-migration.md``.

Interface:

* :class:`CoreStorage` — bundles the two stores under one handle.
* :class:`CoreTransaction` — context manager that acquires both
  per-store locks in a deterministic order and releases them
  on exit.
* :meth:`CoreTransaction.rollback` — retained for API parity with
  a future true-transaction implementation, but in R1.5 it has
  no effect. It exists so call sites that pass ``tx`` into a
  Rust-backed implementation do not need to change when the
  boundary becomes a real transaction.

R1.5 contract (accurate):

* Successful exit: both locks released. The two stores are
  each independently committed.
* Exception exit: both locks released. No cross-DB rollback.
* Crash mid-commit: SQLite transactional semantics apply
  per-file. The worst-case observable split-brain is a ledger
  event without its idempotency state. The inverse is
  impossible in the current Python write order (idem is
  committed first, then the ledger event).
* Authority switch: when ``DELTA_RUST_AUTHORITY`` declares
  ``idempotency`` or ``ledger`` as a Rust domain, the writes
  go through the unified ``delta_core`` process. The cross-DB
  atomicity boundary lands in a later Rust Core phase.
"""

from __future__ import annotations

import logging
import threading

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

    Future R1.5+: the Rust implementation will replace this with a
    single ``core_storage.open(path) -> CoreStorage`` whose
    ``begin/commit/rollback`` methods wrap a real Rust transaction
    across the two underlying DBs. Until that lands, this class
    is a coordinated lock-ordering helper, not an atomic boundary.
    """

    def __init__(self, idem: IdempotencyLog, ledger: RunEventLedger) -> None:
        self.idem = idem
        self.ledger = ledger
        self._lock = threading.RLock()

    def begin(self) -> "CoreTransaction":
        """Acquire both stores' locks in deterministic order."""
        return CoreTransaction(self)


class CoreTransaction:
    """Coordinated lock-ordering boundary across the two core stores.

    Use as a context manager. The ``with`` block acquires the
    idempotency lock first, then the ledger lock, and releases
    both on exit. This prevents concurrent writers from
    interleaving a high-consequence operation (SideEffect intent /
    commit, Run completion) across the two stores.

    Example::

        with storage.begin() as tx:
            tx.idem.commit(...)
            tx.ledger.append(...)

    R1.5 contract (P0-5):

    * Successful exit: both per-store locks released. The two
      stores are each independently committed (two separate
      SQLite DB files).
    * Exception exit: both per-store locks released. **No
      cross-DB rollback.** Whatever each store committed stays
      committed.
    * :meth:`rollback` is a no-op in R1.5. It exists for API
      parity with the future Rust-backed implementation that
      will wrap both stores in a single Rust transaction.

    R1.5 scope: this is the canonical hook for "SideEffect intent",
    "SideEffect commit", "Run completion" high-consequence
    operations when the engine wants coordinated lock ordering.
    The Engine's ``_execute_sync`` path uses the per-store
    delegates directly today; this boundary is available for
    callers that need the deterministic lock order.
    """

    def __init__(self, storage: CoreStorage) -> None:
        self._storage = storage
        self._acquired = False
        self._rolled_back = False

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
        """No-op in R1.5.

        Retained for API parity with the future Rust-backed
        implementation. Python cannot roll back across two
        separate SQLite DB files; the rollback is a no-op
        until ``delta_core`` exposes a true cross-DB
        transaction. Calling sites that need to compensate
        on failure should do so explicitly.
        """
        if self._rolled_back:
            return
        self._rolled_back = True
        _LOG.debug(
            "CoreTransaction.rollback is a no-op in R1.5; "
            "true cross-DB rollback lands with the Rust Core phase"
        )

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
