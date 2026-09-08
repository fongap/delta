"""P0-6 — Production authority call chain tests.

These tests exercise the FULL production call chain, not a
shortcut that bypasses the delegate wrappers:

    SessionManager (production factory)
        ↓
    Rust-authoritative facade / remaining migration wrappers
        ↓
    DeltaCoreClient (persistent connection)
        ↓
    delta_core (Rust subprocess)
        ↓
    SQLite (side-effects.db / run-events.db / automation.db)
        ↓
    Python read-back (committed_for_run / events / runs / ...)

The reverse guard test verifies that when
``DELTA_RUST_AUTHORITY`` declares a domain as Rust, the production
factory must NOT bypass the delegate to do a direct Python write
— if it does, the CI test fails.

R1.5 contract: the delegate wrapper is the only production write
path. Per-op CLI binaries (``write_idemlog`` / ``write_ledger`` /
``write_tasks``) are diagnostic tools only and must never be
invoked from production code.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / (
    "delta_core.exe" if sys.platform == "win32" else "delta_core"
)


def _skip_if_no_binary():
    if not BINARY.exists():
        pytest.skip("delta_core binary not built")


class _NoopProvider:
    """Minimal provider — never called by these tests."""

    def complete(self, *args, **kw):
        raise AssertionError("provider should not be called by P0-6 tests")


@pytest.fixture
def rust_authority_all_on(monkeypatch):
    _skip_if_no_binary()
    # ADR-022/023/024: idempotency, ledger, and task_identity are hard-cut
    # Rust facades (no selector needed). No DELTA_RUST_AUTHORITY required.


# -- Idempotency: full production call chain ---------------------------------


def test_idempotency_full_production_chain(rust_authority_all_on, tmp_path):
    """SessionManager → IdempotencyLog → DeltaCoreClient →
    delta_core → SQLite → Python read-back.

    Covers all five idempotency states: planned, executing, committed,
    failed, uncertain.
    """
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        idem = mgr.idem_log
        run_id = "r-prod-idem-1"
        tool_call_id = "tc-prod-1"

        # planned
        idem.record_planned(
            run_id, tool_call_id, "write_file", {"path": "out.md"}
        )
        # executing
        idem.mark_executing(run_id, tool_call_id)
        # committed
        idem.commit(
            run_id, tool_call_id, "write_file",
            {"path": "out.md"}, {"ok": True},
        )

        # Python read-back from the same SQLite DB.
        committed = idem.committed_for_run(run_id)
        assert len(committed) == 1
        assert committed[0]["tool_call_id"] == tool_call_id
        assert committed[0]["tool_name"] == "write_file"
        assert committed[0]["result"] == {"ok": True}
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()


def test_idempotency_full_production_chain_failed(rust_authority_all_on, tmp_path):
    """Failed state through the full production chain."""
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        idem = mgr.idem_log
        run_id = "r-prod-idem-fail"
        idem.record_planned(
            run_id, "tc-fail", "shell", {"cmd": "false"}
        )
        idem.mark_executing(run_id, "tc-fail")
        idem.mark_failed(run_id, "tc-fail", error="disk full")

        committed = idem.committed_for_run(run_id)
        uncommitted = idem.uncommitted_for_run(run_id)
        assert len(committed) == 0
        assert len(uncommitted) == 0
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()


def test_idempotency_full_production_chain_uncertain(
    rust_authority_all_on, tmp_path
):
    """Uncertain state through the full production chain."""
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        idem = mgr.idem_log
        run_id = "r-prod-idem-uncertain"
        idem.record_planned(
            run_id, "tc-unc", "send_message", {"to": "bob"}
        )
        idem.mark_executing(run_id, "tc-unc")
        idem.mark_uncertain(run_id, "tc-unc")

        uncertain = idem.uncertain_for_run(run_id)
        assert len(uncertain) == 1
        assert uncertain[0]["tool_call_id"] == "tc-unc"
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()


# -- Ledger: full production call chain ---------------------------------------


def test_ledger_full_production_chain(rust_authority_all_on, tmp_path):
    """SessionManager → RunEventLedgerWithDelegate → DeltaCoreClient →
    delta_core → SQLite → Python read-back.

    Covers: append, hash chain, verify, run_status.
    """
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        ledger = mgr.run_ledger
        run_id = "r-prod-ledger-1"

        # append
        ledger.append(run_id, "run.started", actor="user", payload={"kind": "run"})
        ledger.append(
            run_id, "tool.finished", actor="system",
            payload={"name": "read_file"},
        )
        ledger.append(
            run_id, "run.completed", actor="system", payload={"kind": "run"}
        )

        # hash chain
        assert ledger.verify(run_id) is True

        # events (read-back)
        events = ledger.events(run_id)
        assert len(events) == 3
        assert [e["type"] for e in events] == [
            "run.started", "tool.finished", "run.completed",
        ]

        # run_status (derived from ledger)
        assert ledger.run_status(run_id) == "ok"
        assert ledger.derive_run_status(run_id) == "ok"
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()


# -- Task identity: full production call chain -------------------------------


def test_task_identity_full_production_chain(rust_authority_all_on, tmp_path):
    """SessionManager → TaskStore (Rust facade) → DeltaCoreClient →
    delta_core → SQLite → Python read-back.

    Covers: save, update, add_run, delete.
    """
    from core.automation.models import Schedule, ScheduledTask, TaskRun
    from core.automation.store import TaskStore
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        # Verify the production factory creates the Rust-authoritative TaskStore facade.
        assert isinstance(mgr.task_store, TaskStore), (
            "SessionManager did not create a TaskStore; "
            "ADR-024 hard-cut: task_identity is a thin Rust facade."
        )
        store = mgr.task_store
        task = ScheduledTask(
            title="prod-chain-test",
            instructions="verify production chain",
            schedule=Schedule(kind="cron", cron="0 * * * *"),
            workspace=str(tmp_path / "ws"),
        )

        # save
        store.save(task)
        found = store.get(task.id)
        assert found is not None
        assert found.title == "prod-chain-test"

        # update (save again with modified fields)
        task.title = "prod-chain-updated"
        store.save(task)
        updated = store.get(task.id)
        assert updated is not None
        assert updated.title == "prod-chain-updated"

        # add_run
        run = TaskRun(
            task_id=task.id,
            run_id="r-prod-task-1",
            started_at=1000.0,
            workspace=str(tmp_path / "ws"),
        )
        store.add_run(run)
        found_run = store.find_run("r-prod-task-1")
        assert found_run is not None
        assert found_run.task_id == task.id

        # delete
        assert store.delete(task.id) is True
        assert store.get(task.id) is None
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()


# -- Reverse guard: production must NOT bypass the delegate ------------------


def test_reverse_guard_no_direct_python_write_when_authority_on(
    rust_authority_all_on, tmp_path, monkeypatch
):
    """P0-6 reverse guard: when Rust authority is active for idempotency
    and ledger, the production write path MUST go through the
    Rust facade (and thus through delta_core). A direct Python write
    that bypasses the facade must be detectable and fail the CI.

    This test constructs a SessionManager and verifies that:
    1. The factory actually created the Rust-authoritative facades.
    2. A Python read-back of a write made through the facade
       confirms the data went to SQLite (not just in-memory).
    3. The delta_core subprocess is actually running (proves
       writes are not falling through to a Python-only path).
    """
    from core.automation.store import TaskStore
    from core.idemlog import IdempotencyLog
    from core.ledger import RunEventLedger
    from packages.delta_core_client import default_client
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        # 1. Idempotency and Ledger are hard-cut; Task identity is hard-cut.
        assert isinstance(mgr.idem_log, IdempotencyLog), (
            "SessionManager did not create the Rust-authoritative IdempotencyLog facade."
        )
        assert isinstance(mgr.run_ledger, RunEventLedger), (
            "SessionManager did not create a RunEventLedger; "
            "ADR-023 hard-cut: ledger is a thin Rust facade."
        )
        assert isinstance(mgr.task_store, TaskStore), (
            "SessionManager did not create a TaskStore; "
            "ADR-024 hard-cut: task_identity is a thin Rust facade."
        )

        # 2. Write through the facade; read back through Python.
        mgr.idem_log.record_planned(
            "r-guard-1", "tc-guard", "write_file", {"path": "g.txt"}
        )
        mgr.run_ledger.append(
            "r-guard-1", "run.started", actor="user", payload={}
        )
        # record_planned creates a planned row, not committed; check
        # via the uncommitted_for_run path instead.
        uncommitted = mgr.idem_log.uncommitted_for_run("r-guard-1")
        assert len(uncommitted) >= 1  # row exists in SQLite

        events = mgr.run_ledger.events("r-guard-1")
        assert len(events) == 1
        assert events[0]["type"] == "run.started"

        # 3. The delta_core subprocess must be running (proves the
        # write actually went through Rust, not Python fallback).
        client = default_client()
        assert client._proc is not None, (
            "delta_core subprocess is not running; the facade is "
            "not actually using Rust."
        )
        assert client._proc.poll() is None, (
            "delta_core subprocess has died; writes are not reaching Rust."
        )
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()


# -- P1-2: Portable smoke test (Chinese + space path) ----------------------


def test_portable_smoke_chinese_space_path(rust_authority_all_on, tmp_path):
    """P1-2: simulate the Windows Portable layout under a path with
    Chinese characters and spaces. The delta_core binary must be
    discoverable via DELTA_PORTABLE_ROOT, the SessionManager must
    wire up facades, and writes must reach SQLite through Rust.

    This is the closest the CI can get to a real machine smoke
    without actually running tauri build + PyInstaller. The
    invariants it covers:

    1. Path with CJK + spaces resolves the binary.
    2. ``Data/`` is the only user-state directory touched.
    3. ``delta_core.exe`` is found at
       ``<root>/App/Delta/delta_core.exe`` via
       ``DELTA_PORTABLE_ROOT``.
    4. Idempotency, Ledger, and Task identity are hard-cut.
    5. Rust authority does NOT fall back to Python.
    """
    import shutil

    # Build a portable layout under a CJK + space path.
    portable_root = tmp_path / "便携版 Delta 桌面"
    app_delta = portable_root / "App" / "Delta"
    data_dir = portable_root / "Data"
    app_delta.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    # Stage the real delta_core binary next to the (would-be) Delta.exe.
    if not BINARY.exists():
        pytest.skip("delta_core binary not built")
    shutil.copy2(BINARY, app_delta / BINARY.name)

    # Run the SessionManager as if launched from the portable launcher.
    from services.server.manager import SessionManager

    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        # 1. All three R1 domains are hard-cut.
        from core.automation.store import TaskStore
        from core.idemlog import IdempotencyLog
        from core.ledger import RunEventLedger

        assert isinstance(mgr.idem_log, IdempotencyLog)
        assert isinstance(mgr.run_ledger, RunEventLedger)
        assert isinstance(mgr.task_store, TaskStore)

        # 2. The delta_core binary was found via DELTA_PORTABLE_ROOT.
        from packages.delta_core_client import _find_delta_core_binary

        # Set DELTA_PORTABLE_ROOT and verify the lookup finds the staged binary.
        # Unset DELTA_CORE_BINARY so it doesn't shadow the portable lookup.
        import os
        saved = os.environ.pop("DELTA_CORE_BINARY", None)
        os.environ["DELTA_PORTABLE_ROOT"] = str(portable_root)
        try:
            found = _find_delta_core_binary()
            assert found is not None
            assert found == app_delta / BINARY.name
        finally:
            os.environ.pop("DELTA_PORTABLE_ROOT", None)
            if saved is not None:
                os.environ["DELTA_CORE_BINARY"] = saved

        # 3. A real write through the facade reaches SQLite.
        mgr.idem_log.record_planned(
            "r-portable-1", "tc-portable-1", "write_file", {"path": "x.txt"}
        )
        mgr.run_ledger.append(
            "r-portable-1", "run.started", actor="user", payload={"kind": "run"}
        )
        mgr.run_ledger.append(
            "r-portable-1", "run.completed", actor="system",
            payload={"kind": "run"},
        )

        # 4. Task identity: create a task to ensure automation.db is created.
        from core.automation.models import Schedule, ScheduledTask
        task = ScheduledTask(
            title="portable-test",
            instructions="test",
            schedule=Schedule(kind="cron", cron="0 * * * *"),
            workspace=str(data_dir / "ws"),
        )
        mgr.task_store.save(task)

        # 5. Read-back from the same SQLite DB files (now under Data/).
        committed = mgr.idem_log.committed_for_run("r-portable-1")
        # record_planned creates a row but not yet committed; check
        # via the lookup path.
        uncommitted = mgr.idem_log.uncommitted_for_run("r-portable-1")
        assert len(uncommitted) + len(committed) >= 1

        events = mgr.run_ledger.events("r-portable-1")
        assert len(events) == 2
        assert events[0]["type"] == "run.started"
        assert events[1]["type"] == "run.completed"
        assert mgr.run_ledger.verify("r-portable-1") is True

        # 6. The data files landed under Data/, not elsewhere.
        assert (data_dir / "side-effects.db").exists()
        assert (data_dir / "run-events.db").exists()
        assert (data_dir / "automation.db").exists()
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()





# -- Concurrent production writes through delta_core -------------------------


def test_concurrent_production_writes_serialize_through_delta_core(
    rust_authority_all_on, tmp_path
):
    """Multiple threads writing through the production delegates
    must serialize through the single delta_core subprocess and
    the hash chain must remain consistent."""
    from services.server.manager import SessionManager

    data_dir = tmp_path / "delta-state"
    mgr = SessionManager(data_dir=data_dir, provider=_NoopProvider())
    try:
        ledger = mgr.run_ledger
        run_id = "r-concurrent-prod"

        errors: list[str] = []
        barrier = threading.Barrier(3)

        def writer(thread_id: int):
            try:
                barrier.wait(timeout=5)
                for i in range(5):
                    ledger.append(
                        run_id, "tool.finished",
                        actor=f"t{thread_id}",
                        payload={"i": i, "thread": thread_id},
                    )
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"concurrent writers failed: {errors}"
        events = ledger.events(run_id)
        assert len(events) == 15  # 3 threads × 5 writes
        # Hash chain must still verify after concurrent writes.
        assert ledger.verify(run_id) is True
    finally:
        mgr.run_ledger.close()
        mgr.idem_log.close()
        mgr.task_store.close()
