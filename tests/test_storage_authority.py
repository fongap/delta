"""Per-domain Rust authority selector tests (ADR-012 / P0-C / R1.7 P0-1).

Tests the ``DELTA_RUST_AUTHORITY`` env var parser for per-domain
control. The parser replaces the old global boolean semantics with
a comma-separated domain list, while maintaining backward
compatibility with legacy ``1``/``true``/``yes``/``on`` spellings.

R1.7 (P0-1): unknown domains raise, derived/coordination domains
raise, ``all`` maps only to ``RUST_WRITE_DOMAINS``.
"""

from __future__ import annotations

import os
import pytest

from packages.storage_authority import (
    RUST_WRITE_DOMAINS,
    RUST_READ_DOMAINS,
    DERIVED_DOMAINS,
    COORDINATION_DOMAINS,
    ALL_DOMAINS,
    _parse_domains,
    is_rust_authority,
    UnknownDomainError,
    InvalidAuthorityTargetError,
)


def os_env() -> str | None:
    return os.environ.get("DELTA_RUST_AUTHORITY")


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
    assert _parse_domains(os_env()) == frozenset(RUST_WRITE_DOMAINS)


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "Idempotency,Ledger")
    assert _parse_domains(os_env()) == frozenset({"idempotency", "ledger"})


def test_whitespace_normalized(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "  idempotency , ledger  ")
    assert _parse_domains(os_env()) == frozenset({"idempotency", "ledger"})


def test_run_state_rejected(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "run_state")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "derived from" in str(exc.value)
    assert "ledger" in str(exc.value)


def test_storage_transaction_rejected(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "storage_transaction")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "coordination boundary" in str(exc.value)


def test_unknown_domain_raises(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency,fictional_domain")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "fictional_domain" in str(exc.value)


def test_only_unknown_domains_raises(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "fictional_a,fictional_b")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "fictional_a" in str(exc.value) and "fictional_b" in str(exc.value)


def test_mixed_known_unknown_raises(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency,fictional_a,unknown_b")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "fictional_a" in str(exc.value)
    assert "unknown_b" in str(exc.value)


# -- legacy compatibility ----------------------------------------------------


@pytest.mark.parametrize("legacy", ["1", "true", "yes", "on", "TRUE", "Yes", "ON"])
def test_legacy_truthy_equivalent_to_all(monkeypatch, legacy):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", legacy)
    assert _parse_domains(os_env()) == frozenset(RUST_WRITE_DOMAINS)


def test_legacy_truthy_uppercase(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "TRUE")
    for d in RUST_WRITE_DOMAINS:
        assert is_rust_authority(d)


# -- is_rust_authority per-domain tests --------------------------------------


def test_unset_means_python_for_all_domains(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)
    for d in RUST_WRITE_DOMAINS:
        assert not is_rust_authority(d)


def test_idempotency_only(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")
    assert is_rust_authority("idempotency")
    assert not is_rust_authority("ledger")
    assert not is_rust_authority("task_identity")


def test_idempotency_and_ledger(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency,ledger")
    assert is_rust_authority("idempotency")
    assert is_rust_authority("ledger")
    assert not is_rust_authority("task_identity")


def test_all_enables_every_rust_write_domain(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "all")
    for d in RUST_WRITE_DOMAINS:
        assert is_rust_authority(d)


def test_unknown_domain_raises_unknown_domain_error(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "all")
    with pytest.raises(InvalidAuthorityTargetError) as exc:
        is_rust_authority("unknown_domain")
    assert "unknown domain" in str(exc.value)


def test_legacy_1_enables_rust_write_domains(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "1")
    for d in RUST_WRITE_DOMAINS:
        assert is_rust_authority(d)


# -- is_rust_authority invalid target errors ---------------------------------


def test_run_state_raises_invalid_target():
    with pytest.raises(InvalidAuthorityTargetError) as exc:
        is_rust_authority("run_state")
    assert "derived from" in str(exc.value)
    assert "ledger" in str(exc.value)


def test_storage_transaction_raises_invalid_target():
    with pytest.raises(InvalidAuthorityTargetError) as exc:
        is_rust_authority("storage_transaction")
    assert "coordination boundary" in str(exc.value)


def test_unknown_domain_raises_invalid_target():
    with pytest.raises(InvalidAuthorityTargetError) as exc:
        is_rust_authority("unknown_domain")
    assert "unknown domain" in str(exc.value)


# -- constants ---------------------------------------------------------------


def test_constants_consistency():
    # Derived and coordination are subsets of ALL_DOMAINS
    assert DERIVED_DOMAINS.keys() <= ALL_DOMAINS
    assert COORDINATION_DOMAINS <= ALL_DOMAINS
    # R2 reader domains are subsets of ALL_DOMAINS but disjoint from R1
    assert RUST_READ_DOMAINS <= ALL_DOMAINS
    assert RUST_WRITE_DOMAINS.isdisjoint(RUST_READ_DOMAINS)
    # RUST_WRITE_DOMAINS are ALL_DOMAINS minus derived/coordination/readers
    assert RUST_WRITE_DOMAINS == (
        ALL_DOMAINS
        - frozenset(DERIVED_DOMAINS)
        - COORDINATION_DOMAINS
        - RUST_READ_DOMAINS
    )
    # run_state derived from ledger
    assert DERIVED_DOMAINS["run_state"] == "ledger"
    # ALL_DOMAINS does not include unknown domains
    assert "unknown_domain" not in ALL_DOMAINS
    # R2 reader env var is distinct from R1 write env var
    from packages.storage_authority import READER_ENV_VAR
    assert READER_ENV_VAR != "DELTA_RUST_AUTHORITY"


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


# -- R2 shadow-reader API (ADR-019) ------------------------------------------


def test_r2_read_domains_defined():
    from packages.storage_authority import RUST_READ_DOMAINS

    # After PR132 (ADR-020): artifact is promoted to RUST_WRITE_DOMAINS
    # (the Rust write path for artifact.registered / artifact.completed
    # events landed). The remaining reader-only R2 domains are
    # validation / checkpoint. Source/Citation validity was promoted in R2.1.
    assert RUST_READ_DOMAINS == frozenset({
        "validation", "checkpoint",
    })


def test_r2_read_env_var_unset_returns_false(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.delenv("DELTA_RUST_READERS", raising=False)
    assert is_rust_shadow_reader("validation") is False
    assert is_rust_shadow_reader("checkpoint") is False
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("source_citation")


def test_r2_read_specific_domain(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.setenv("DELTA_RUST_READERS", "validation")
    assert is_rust_shadow_reader("validation") is True
    assert is_rust_shadow_reader("checkpoint") is False


def test_r2_read_all_keyword(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.setenv("DELTA_RUST_READERS", "all")
    assert is_rust_shadow_reader("validation") is True
    assert is_rust_shadow_reader("checkpoint") is True
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("source_citation")


def test_r2_read_legacy_truthy(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.setenv("DELTA_RUST_READERS", "1")
    assert is_rust_shadow_reader("validation") is True
    monkeypatch.setenv("DELTA_RUST_READERS", "true")
    assert is_rust_shadow_reader("checkpoint") is True


def test_r2_read_rejects_r1_write_domain(monkeypatch):
    """is_rust_shadow_reader must reject R1 write domains — those
    have write authority, not just a reader. Use is_rust_authority."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_READERS", "validation")
    for r1 in (
        "idempotency", "ledger", "task_identity", "artifact", "source_citation"
    ):
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_shadow_reader(r1)


def test_r2_read_rejects_derived_and_coordination(monkeypatch):
    from packages.storage_authority import UnknownDomainError

    for bad in ("run_state", "storage_transaction"):
        monkeypatch.setenv("DELTA_RUST_READERS", bad)
        with pytest.raises(UnknownDomainError):
            from packages.storage_authority import is_rust_shadow_reader
            is_rust_shadow_reader("validation")


def test_r2_read_rejects_policy_and_approval(monkeypatch):
    """Policy and Approval are evaluation/decision surfaces, not data
    readers. They are NOT in RUST_READ_DOMAINS; their shadow-check
    will be a different hook in their per-domain ADR."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_READERS", "validation")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("policy")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("approval")


def test_r2_read_unknown_domain(monkeypatch):
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("unknown_domain")


def test_r2_read_disjoint_from_r1_write(monkeypatch):
    """R1 write and R2 reader are distinct surfaces. Setting one must
    not affect the other."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_authority, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")
    monkeypatch.setenv("DELTA_RUST_READERS", "validation")
    assert is_rust_authority("idempotency") is True
    # Remaining R2 reader domain is not a valid R1 write target.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("validation")
    assert is_rust_shadow_reader("validation") is True
    # R1 domain is not a valid R2 reader target.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("idempotency")


def test_r1_write_rejects_r2_read_domain(monkeypatch):
    """is_rust_authority must reject remaining R2 reader domains —
    those have a Rust reader but no Rust write authority yet."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_authority,
    )

    for r2 in ("validation", "checkpoint"):
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_authority(r2)


def test_source_citation_is_write_authority_domain(monkeypatch):
    """R2.1 promotes final citation validity from shadow to authority."""
    from packages.storage_authority import (
        RUST_READ_DOMAINS, RUST_WRITE_DOMAINS, is_rust_authority,
    )

    assert "source_citation" in RUST_WRITE_DOMAINS
    assert "source_citation" not in RUST_READ_DOMAINS
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation")
    assert is_rust_authority("source_citation") is True


# -- artifact is now an R1-style write domain (PR132 / ADR-020) -----------


def test_artifact_is_write_domain():
    """Artifact was promoted from RUST_READ_DOMAINS to RUST_WRITE_DOMAINS
    when the Rust write path landed (PR132). Use is_rust_authority
    for the write switch, not is_rust_shadow_reader."""
    from packages.storage_authority import (
        RUST_READ_DOMAINS, RUST_WRITE_DOMAINS,
    )
    assert "artifact" in RUST_WRITE_DOMAINS
    assert "artifact" not in RUST_READ_DOMAINS


def test_artifact_authority_env_var(monkeypatch):
    from packages.storage_authority import is_rust_authority

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "artifact")
    assert is_rust_authority("artifact") is True


def test_artifact_authority_rejects_r1_only(monkeypatch):
    """When only DELTA_RUST_AUTHORITY=artifact is set, the R1 domains
    must NOT be active (per-domain selector)."""
    from packages.storage_authority import is_rust_authority

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "artifact")
    assert is_rust_authority("artifact") is True
    assert is_rust_authority("idempotency") is False
    assert is_rust_authority("ledger") is False
    assert is_rust_authority("task_identity") is False


def test_artifact_rejected_from_r2_reader(monkeypatch):
    """is_rust_shadow_reader must reject artifact (it's now a write
    domain, not a reader)."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_READERS", "validation")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("artifact")
