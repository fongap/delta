"""Per-domain Rust authority selector tests (ADR-012 / P0-C).

Tests the ``DELTA_RUST_AUTHORITY`` env var parser for per-domain
control. The parser replaces the old global boolean semantics with
a comma-separated domain list, while maintaining backward
compatibility with legacy ``1``/``true``/``yes``/``on`` spellings.
"""

from __future__ import annotations

import pytest

from packages.storage_authority import DOMAINS, _parse_domains, is_rust_authority


# -- parser tests ------------------------------------------------------------


def test_unset_env_returns_empty_set(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)
    assert _parse_domains(None) == frozenset()
    assert _parse_domains("") == frozenset()
    assert _parse_domains("   ") == frozenset()


def test_single_domain(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")
    assert _parse_domains(os_env()) == frozenset({"idempotency"})


def test_two_domains(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency,ledger")
    assert _parse_domains(os_env()) == frozenset({"idempotency", "ledger"})


def test_all_keyword(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "all")
    assert _parse_domains(os_env()) == frozenset(DOMAINS)


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "Idempotency,Ledger")
    assert _parse_domains(os_env()) == frozenset({"idempotency", "ledger"})


def test_whitespace_normalized(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "  idempotency , ledger  ")
    assert _parse_domains(os_env()) == frozenset({"idempotency", "ledger"})


def test_unknown_domain_ignored(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency,fictional_domain")
    assert _parse_domains(os_env()) == frozenset({"idempotency"})


def test_only_unknown_domains_returns_empty(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "fictional_a,fictional_b")
    assert _parse_domains(os_env()) == frozenset()


# -- legacy compatibility ----------------------------------------------------


@pytest.mark.parametrize("legacy", ["1", "true", "yes", "on", "TRUE", "Yes", "ON"])
def test_legacy_truthy_equivalent_to_all(monkeypatch, legacy):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", legacy)
    assert _parse_domains(os_env()) == frozenset(DOMAINS)


def test_legacy_truthy_uppercase(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "TRUE")
    for d in DOMAINS:
        assert is_rust_authority(d)


# -- is_rust_authority per-domain tests --------------------------------------


def test_unset_means_python_for_all_domains(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)
    for d in DOMAINS:
        assert not is_rust_authority(d)


def test_idempotency_only(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")
    assert is_rust_authority("idempotency")
    assert not is_rust_authority("ledger")
    assert not is_rust_authority("run_state")
    assert not is_rust_authority("task_identity")
    assert not is_rust_authority("storage_transaction")


def test_idempotency_and_ledger(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency,ledger")
    assert is_rust_authority("idempotency")
    assert is_rust_authority("ledger")
    assert not is_rust_authority("run_state")
    assert not is_rust_authority("task_identity")


def test_all_enables_every_domain(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "all")
    for d in DOMAINS:
        assert is_rust_authority(d)


def test_unknown_domain_returns_false(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "all")
    assert not is_rust_authority("unknown_domain")


def test_legacy_1_enables_every_domain(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "1")
    for d in DOMAINS:
        assert is_rust_authority(d)


# -- per-domain delegate behavior -------------------------------------------


def test_idempotency_only_activates_idempotency_delegate_not_ledger(monkeypatch, tmp_path):
    """DELTA_RUST_AUTHORITY=idempotency must not activate the ledger delegate.

    The delegate modules gate on is_rust_authority(domain) individually,
    so a per-domain env var activates only the matching delegate.
    Binary presence alone must NOT enable all delegates.
    """
    import sys
    from pathlib import Path

    from core.idemlog import IdempotencyLog
    from core.idemlog_delegate import IdempotencyLogWithDelegate, maybe_wrap
    from core.ledger import RunEventLedger
    from core.ledger_delegate import RunEventLedgerWithDelegate, maybe_wrap_ledger

    repo_root = Path(__file__).resolve().parent.parent
    crate_dir = repo_root / "core" / "runtime-native"
    binary = crate_dir / "target" / "debug" / (
        "delta_core.exe" if sys.platform == "win32" else "delta_core"
    )
    if not binary.exists():
        pytest.skip("delta_core binary not built")

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")

    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    wrapped = maybe_wrap(log, str(db))
    assert isinstance(wrapped, IdempotencyLogWithDelegate), (
        "idempotency delegate must activate when DELTA_RUST_AUTHORITY=idempotency"
    )

    led = RunEventLedger(tmp_path / "run_events.db")
    wrapped_ledger = maybe_wrap_ledger(led)
    assert not isinstance(wrapped_ledger, RunEventLedgerWithDelegate), (
        "ledger delegate must NOT activate when only idempotency is in the authority set"
    )


# -- helper ------------------------------------------------------------------


def os_env() -> str | None:
    import os

    return os.environ.get("DELTA_RUST_AUTHORITY")
