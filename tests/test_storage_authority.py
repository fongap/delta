"""Per-domain Rust authority selector tests (ADR-012 / P0-C / R1.7 P0-1).

Tests the ``DELTA_RUST_AUTHORITY`` env var parser for per-domain
control. The parser replaces the old global boolean semantics with
a comma-separated domain list, while maintaining backward
compatibility with legacy ``1``/``true``/``yes``/``on`` spellings.

R1.7 (P0-1): unknown domains raise, derived domains raise, ``all``
maps only to ``RUST_WRITE_DOMAINS``.

R1 Final Convergence (ADR-025): ``storage_transaction`` coordination
domain removed. It is now treated as an unknown domain.
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
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")
    assert _parse_domains(os_env()) == frozenset({"validation"})


def test_two_domains(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "source_citation,validation")
    assert _parse_domains(os_env()) == frozenset({"source_citation", "validation"})


def test_all_keyword(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "all")
    assert _parse_domains(os_env()) == frozenset(RUST_WRITE_DOMAINS)


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "Validation,Source_citation")
    assert _parse_domains(os_env()) == frozenset({"validation", "source_citation"})


def test_whitespace_normalized(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "  validation , source_citation  ")
    assert _parse_domains(os_env()) == frozenset({"validation", "source_citation"})


def test_run_state_rejected(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "run_state")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "derived from" in str(exc.value)
    assert "ledger" in str(exc.value)


def test_storage_transaction_rejected(monkeypatch):
    """storage_transaction is no longer a coordination domain (ADR-025).
    It is treated as an unknown domain."""
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "storage_transaction")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "unknown domain" in str(exc.value)


def test_unknown_domain_raises(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation,fictional_domain")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "fictional_domain" in str(exc.value)


def test_only_unknown_domains_raises(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "fictional_a,fictional_b")
    with pytest.raises(UnknownDomainError) as exc:
        _parse_domains(os_env())
    assert "fictional_a" in str(exc.value) and "fictional_b" in str(exc.value)


def test_mixed_known_unknown_raises(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation,fictional_a,unknown_b")
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


def test_idempotency_is_not_selectable_after_hard_cut(monkeypatch):
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("idempotency")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


def test_ledger_is_not_selectable_after_hard_cut(monkeypatch):
    """ADR-023: ledger is a hard-cut Rust facade, not a selectable authority."""
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "ledger")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("ledger")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


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
    """storage_transaction is no longer a coordination domain (ADR-025)."""
    with pytest.raises(InvalidAuthorityTargetError) as exc:
        is_rust_authority("storage_transaction")
    assert "unknown domain" in str(exc.value)


def test_unknown_domain_raises_invalid_target():
    with pytest.raises(InvalidAuthorityTargetError) as exc:
        is_rust_authority("unknown_domain")
    assert "unknown domain" in str(exc.value)


# -- constants ---------------------------------------------------------------


def test_constants_consistency():
    # Derived are subsets of ALL_DOMAINS
    assert DERIVED_DOMAINS.keys() <= ALL_DOMAINS
    # COORDINATION_DOMAINS is empty after ADR-025 (storage_transaction removed)
    assert COORDINATION_DOMAINS == frozenset()
    assert COORDINATION_DOMAINS.isdisjoint(ALL_DOMAINS)
    # R2 reader domains are subsets of ALL_DOMAINS but disjoint from R1
    assert RUST_READ_DOMAINS <= ALL_DOMAINS
    assert RUST_WRITE_DOMAINS.isdisjoint(RUST_READ_DOMAINS)
    # RUST_WRITE_DOMAINS are ALL_DOMAINS minus derived/coordination/readers
    assert RUST_WRITE_DOMAINS == (
        ALL_DOMAINS
        - frozenset(DERIVED_DOMAINS)
        - RUST_READ_DOMAINS
    )
    # run_state derived from ledger
    assert DERIVED_DOMAINS["run_state"] == "ledger"
    # ALL_DOMAINS does not include unknown domains
    assert "unknown_domain" not in ALL_DOMAINS
    # R2 reader env var is distinct from R1 write env var
    from packages.storage_authority import READER_ENV_VAR
    assert READER_ENV_VAR != "DELTA_RUST_AUTHORITY"


# -- R2 shadow-reader API (ADR-019) ------------------------------------------


def test_r2_read_domains_defined():
    from packages.storage_authority import RUST_READ_DOMAINS

    # After PR132 (ADR-020): artifact is promoted to RUST_WRITE_DOMAINS
    # (the Rust write path for artifact.registered / artifact.completed
    # events landed). The remaining reader-only R2 domains are
    # checkpoint only. Source/Citation and Validation were promoted in R2.
    assert RUST_READ_DOMAINS == frozenset({
        "checkpoint",
    })


def test_r2_read_env_var_unset_returns_false(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.delenv("DELTA_RUST_READERS", raising=False)
    assert is_rust_shadow_reader("checkpoint") is False
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("source_citation")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("validation")


def test_r2_read_specific_domain(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.setenv("DELTA_RUST_READERS", "checkpoint")
    assert is_rust_shadow_reader("checkpoint") is True


def test_r2_read_all_keyword(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.setenv("DELTA_RUST_READERS", "all")
    assert is_rust_shadow_reader("checkpoint") is True
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("source_citation")


def test_r2_read_legacy_truthy(monkeypatch):
    from packages.storage_authority import is_rust_shadow_reader

    monkeypatch.setenv("DELTA_RUST_READERS", "1")
    assert is_rust_shadow_reader("checkpoint") is True
    monkeypatch.setenv("DELTA_RUST_READERS", "true")
    assert is_rust_shadow_reader("checkpoint") is True


def test_r2_read_rejects_r1_write_domain(monkeypatch):
    """is_rust_shadow_reader must reject R1 write domains — those
    have write authority, not just a reader. Use is_rust_authority."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_READERS", "checkpoint")
    for r1 in (
        "idempotency", "ledger", "task_identity", "source_citation",
        "validation",
    ):
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_shadow_reader(r1)


def test_r2_read_rejects_derived_and_unknown(monkeypatch):
    from packages.storage_authority import UnknownDomainError

    for bad in ("run_state", "storage_transaction"):
        monkeypatch.setenv("DELTA_RUST_READERS", bad)
        with pytest.raises(UnknownDomainError):
            from packages.storage_authority import is_rust_shadow_reader
            is_rust_shadow_reader("checkpoint")


def test_r2_read_rejects_policy_and_approval(monkeypatch):
    """Policy and Approval are evaluation/decision surfaces, not data
    readers. They are NOT in RUST_READ_DOMAINS; their shadow-check
    will be a different hook in their per-domain ADR."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_READERS", "checkpoint")
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

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")
    monkeypatch.setenv("DELTA_RUST_READERS", "checkpoint")
    assert is_rust_authority("validation") is True
    # ADR-023: ledger is a hard-cut facade, not a selectable authority.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("ledger")
    # ADR-024: task_identity is a hard-cut facade, not a selectable authority.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("task_identity")
    # ADR-026: artifact is hard-cut, not a selectable authority.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("artifact")
    # Remaining R2 reader domain is not a valid write target.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("checkpoint")
    assert is_rust_shadow_reader("checkpoint") is True
    # R1 domain is not a valid R2 reader target.
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("idempotency")


def test_r1_write_rejects_r2_read_domain(monkeypatch):
    """is_rust_authority must reject remaining R2 reader domains —
    those have a Rust reader but no Rust write authority yet."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_authority,
    )

    for r2 in ("checkpoint",):
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


def test_validation_is_write_authority_domain(monkeypatch):
    from packages.storage_authority import (
        RUST_READ_DOMAINS, RUST_WRITE_DOMAINS, is_rust_authority,
    )

    assert "validation" in RUST_WRITE_DOMAINS
    assert "validation" not in RUST_READ_DOMAINS
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "validation")
    assert is_rust_authority("validation") is True


# -- artifact is hard-cut to Rust (ADR-026) --------------------------------


def test_artifact_is_not_a_selectable_write_domain():
    """ADR-026: artifact is hard-cut to Rust and is no longer a
    selectable authority. It is not in RUST_WRITE_DOMAINS and not in
    RUST_READ_DOMAINS."""
    from packages.storage_authority import (
        RUST_READ_DOMAINS, RUST_WRITE_DOMAINS,
    )
    assert "artifact" not in RUST_WRITE_DOMAINS
    assert "artifact" not in RUST_READ_DOMAINS


def test_artifact_authority_env_var_is_rejected(monkeypatch):
    """DELTA_RUST_AUTHORITY=artifact must raise (artifact is hard-cut)."""
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "artifact")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("artifact")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


def test_artifact_rejected_from_r2_reader(monkeypatch):
    """is_rust_shadow_reader must reject artifact (it is neither a
    write domain nor a shadow-reader domain after ADR-026)."""
    from packages.storage_authority import (
        InvalidAuthorityTargetError, is_rust_shadow_reader,
    )

    monkeypatch.setenv("DELTA_RUST_READERS", "checkpoint")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("artifact")
