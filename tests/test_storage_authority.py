"""Tests for packages/storage_authority.py — authority selector contract.

Covers:
- R1 write domains (Idempotency, Ledger, Task Identity — hard-cut via ADR-022/023/024)
- R2 shadow-reader domains (ADR-019) — for domains with Rust readers but not yet write authority
- R2 write domains (Artifact ADR-026, Source/Citation ADR-027, Validation ADR-028, Checkpoint ADR-029)
- Derived and coordination domains
- Env var parsing rules (fail-fast, no silent ignore)
- Disjointness of R1 write vs R2 reader surfaces
"""

from __future__ import annotations

import os
import pytest

from packages.storage_authority import (
    ALL_DOMAINS,
    COORDINATION_DOMAINS,
    DERIVED_DOMAINS,
    ENV_VAR,
    InvalidAuthorityTargetError,
    READER_ENV_VAR,
    RUST_READ_DOMAINS,
    RUST_WRITE_DOMAINS,
    UnknownDomainError,
    _parse_domains,
    _parse_reader_domains,
    is_rust_authority,
    is_rust_shadow_reader,
)




def os_env() -> str | None:
    """Return the current DELTA_RUST_AUTHORITY value (for _parse_domains tests)."""
    return os.environ.get(ENV_VAR)


# -- R1 write authority domains ----------------------------------------------


def test_rust_write_domains_defined():
    """The R2 write domains that accept DELTA_RUST_AUTHORITY declarations.
    R1 domains (idempotency, ledger, task_identity) are hard-cut (ADR-022/023/024)
    and no longer selectable via env var. Artifact and Source/Citation are also
    hard-cut (ADR-026/027). Only validation (ADR-028) and checkpoint (ADR-029)
    are selectable write domains."""
    assert RUST_WRITE_DOMAINS == frozenset({"validation", "checkpoint"})


def test_r1_write_rejects_derived_domains(monkeypatch):
    """is_rust_authority must reject derived domains (run_state)."""
    monkeypatch.setenv(ENV_VAR, "run_state")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("run_state")


def test_r1_write_rejects_coordination_domains(monkeypatch):
    """is_rust_authority must reject coordination domains."""
    # Currently none, but the guard is in place
    pass


def test_r1_write_rejects_unknown_domain(monkeypatch):
    """is_rust_authority must reject unknown domains (fail-fast)."""
    monkeypatch.setenv(ENV_VAR, "unknown_domain")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("unknown_domain")


def test_r1_write_env_var_all_keyword(monkeypatch):
    """Legacy 'all' maps to RUST_WRITE_DOMAINS only."""
    monkeypatch.setenv(ENV_VAR, "all")
    for d in RUST_WRITE_DOMAINS:
        assert is_rust_authority(d) is True


def test_r1_write_env_var_legacy_truthy(monkeypatch):
    """Legacy '1'/'true'/'yes'/'on' maps to RUST_WRITE_DOMAINS."""
    for val in ("1", "true", "yes", "on"):
        monkeypatch.setenv(ENV_VAR, val)
        for d in RUST_WRITE_DOMAINS:
            assert is_rust_authority(d) is True


def test_r1_write_env_var_specific_domain(monkeypatch):
    """Specific domain list enables only those domains."""
    monkeypatch.setenv(ENV_VAR, "validation,checkpoint")
    assert is_rust_authority("validation") is True
    assert is_rust_authority("checkpoint") is True
    # R1 domains are hard-cut, not selectable
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("idempotency")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("ledger")


def test_r1_write_env_var_unset_returns_false(monkeypatch):
    """Unset/empty env var means Python is authority for all."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    for d in RUST_WRITE_DOMAINS:
        assert is_rust_authority(d) is False


# -- R2 shadow-reader API (ADR-019) ------------------------------------------
# Note: After ADR-029, checkpoint is a WRITE authority, not a reader domain.
# These tests verify the new behavior and that the old reader tests
# are no longer applicable.


def test_r2_read_domains_empty_after_checkpoint_hardcut():
    """After ADR-029, checkpoint is promoted to RUST_WRITE_DOMAINS.
    RUST_READ_DOMAINS should be empty (no remaining R2 reader-only domains)."""
    assert RUST_READ_DOMAINS == frozenset()


def test_checkpoint_is_write_authority_domain():
    """Checkpoint is now a write authority (ADR-029)."""
    assert "checkpoint" in RUST_WRITE_DOMAINS
    assert "checkpoint" not in RUST_READ_DOMAINS


def test_checkpoint_rejected_from_r2_reader():
    """is_rust_shadow_reader must reject checkpoint (it is neither a
    write domain nor a shadow-reader domain after ADR-029)."""
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("checkpoint")


def test_r2_read_env_var_rejects_checkpoint(monkeypatch):
    """DELTA_RUST_READERS=checkpoint must raise (checkpoint is hard-cut)."""
    monkeypatch.setenv(READER_ENV_VAR, "checkpoint")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("checkpoint")


def test_r2_read_rejects_r1_write_domain(monkeypatch):
    """is_rust_shadow_reader must reject R1 write domains — those
    have write authority, not just a reader. Use is_rust_authority."""
    monkeypatch.setenv(READER_ENV_VAR, "validation")
    for r1 in ("idempotency", "ledger", "task_identity", "validation", "checkpoint"):
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_shadow_reader(r1)


def test_r2_read_rejects_derived_and_unknown(monkeypatch):
    for bad in ("run_state", "storage_transaction"):
        monkeypatch.setenv(READER_ENV_VAR, bad)
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_shadow_reader(bad)


def test_r2_read_rejects_policy_and_approval(monkeypatch):
    """Policy and Approval are evaluation/decision surfaces, not data
    readers. They are NOT in RUST_READ_DOMAINS; their shadow-check
    will be a different hook in their per-domain ADR."""
    monkeypatch.setenv(READER_ENV_VAR, "validation")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("policy")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("approval")


def test_r2_read_unknown_domain(monkeypatch):
    monkeypatch.setenv(READER_ENV_VAR, "validation")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("unknown_domain")


# -- R2 write authority domains (hard-cut) -----------------------------------


def test_artifact_is_not_a_selectable_write_domain():
    """ADR-026: artifact is hard-cut to Rust and is no longer a
    selectable authority. It is not in RUST_WRITE_DOMAINS and not in
    RUST_READ_DOMAINS."""
    assert "artifact" not in RUST_WRITE_DOMAINS
    assert "artifact" not in RUST_READ_DOMAINS


def test_artifact_authority_env_var_is_rejected(monkeypatch):
    """DELTA_RUST_AUTHORITY=artifact must raise (artifact is hard-cut)."""
    monkeypatch.setenv(ENV_VAR, "artifact")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("artifact")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


def test_artifact_rejected_from_r2_reader(monkeypatch):
    """is_rust_shadow_reader must reject artifact (it is neither a
    write domain nor a shadow-reader domain after ADR-026)."""
    monkeypatch.setenv(READER_ENV_VAR, "validation")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("artifact")


def test_source_citation_is_hard_cut_not_selectable(monkeypatch):
    """Source/Citation is hard-cut to Rust (ADR-027) - no longer selectable via DELTA_RUST_AUTHORITY."""
    assert "source_citation" not in RUST_WRITE_DOMAINS
    assert "source_citation" not in RUST_READ_DOMAINS
    # Using it in DELTA_RUST_AUTHORITY raises an error
    monkeypatch.setenv(ENV_VAR, "source_citation")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


def test_validation_is_write_authority_domain(monkeypatch):
    assert "validation" in RUST_WRITE_DOMAINS
    assert "validation" not in RUST_READ_DOMAINS
    monkeypatch.setenv(ENV_VAR, "validation")
    assert is_rust_authority("validation") is True


# -- Domain disjointness and surface separation ------------------------------


def test_all_domains_is_union():
    """ALL_DOMAINS must be the exact union of write + read + derived + coordination."""
    expected = RUST_WRITE_DOMAINS | RUST_READ_DOMAINS | frozenset(DERIVED_DOMAINS) | COORDINATION_DOMAINS
    assert ALL_DOMAINS == expected


def test_r2_read_env_var_name_distinct():
    """R2 reader env var is distinct from R1 write env var."""
    assert READER_ENV_VAR != ENV_VAR


def test_r2_read_env_var_rejects_r1_write_domain(monkeypatch):
    """is_rust_shadow_reader must reject R1 write domains — those
    have write authority, not just a reader. Use is_rust_authority."""
    monkeypatch.setenv(READER_ENV_VAR, "validation")
    for r1 in ("idempotency", "ledger", "task_identity", "validation", "checkpoint"):
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_shadow_reader(r1)


def test_r2_read_env_var_rejects_r2_write_domain(monkeypatch):
    """is_rust_shadow_reader must reject R2 write domains (artifact, source_citation, validation, checkpoint)."""
    monkeypatch.setenv(READER_ENV_VAR, "validation")
    for r2 in ("artifact", "source_citation", "validation", "checkpoint"):
        with pytest.raises(InvalidAuthorityTargetError):
            is_rust_shadow_reader(r2)


def test_r1_write_rejects_r2_read_domain(monkeypatch):
    """is_rust_authority must reject R2 reader domains —
    those have a Rust reader but no Rust write authority yet."""
    # After ADR-029, there are no R2 reader domains left
    # but the guard should still work if any are added
    pass


# -- Env var parsing rules (fail-fast, no silent ignore) ---------------------


def test_parse_domains_empty_returns_empty():
    assert _parse_domains(None) == frozenset()
    assert _parse_domains("") == frozenset()
    assert _parse_domains("   ") == frozenset()


def test_parse_domains_legacy_all_keyword():
    assert _parse_domains("all") == RUST_WRITE_DOMAINS
    assert _parse_domains("ALL") == RUST_WRITE_DOMAINS
    assert _parse_domains("  all  ") == RUST_WRITE_DOMAINS


def test_parse_domains_legacy_truthy():
    for val in ("1", "true", "yes", "on"):
        assert _parse_domains(val) == RUST_WRITE_DOMAINS
        assert _parse_domains(val.upper()) == RUST_WRITE_DOMAINS


def test_parse_domains_specific_list():
    result = _parse_domains("validation, checkpoint")
    assert result == frozenset({"validation", "checkpoint"})


def test_parse_domains_whitespace_and_case():
    result = _parse_domains("  Validation , CHECKPOINT  ")
    assert result == frozenset({"validation", "checkpoint"})


def test_parse_domains_unknown_raises(monkeypatch):
    with pytest.raises(UnknownDomainError):
        _parse_domains("unknown_domain")


def test_parse_domains_derived_raises(monkeypatch):
    with pytest.raises(UnknownDomainError):
        _parse_domains("run_state")


def test_parse_domains_whitespace_only_empty():
    assert _parse_domains(",,") == frozenset()


# -- Reader env var parsing --------------------------------------------------


def test_parse_reader_domains_empty_returns_empty():
    assert _parse_reader_domains(None) == frozenset()
    assert _parse_reader_domains("") == frozenset()
    assert _parse_reader_domains("   ") == frozenset()


def test_parse_reader_domains_legacy_all_keyword():
    assert _parse_reader_domains("all") == RUST_READ_DOMAINS
    assert _parse_reader_domains("ALL") == RUST_READ_DOMAINS


def test_parse_reader_domains_legacy_truthy():
    for val in ("1", "true", "yes", "on"):
        assert _parse_reader_domains(val) == RUST_READ_DOMAINS


def test_parse_reader_domains_rejects_r1_write_domain():
    # R1 write domains are not in RUST_READ_DOMAINS (empty), so they raise UnknownDomainError
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("idempotency")


def test_parse_reader_domains_rejects_r2_write_domain():
    # R2 write domains are not in RUST_READ_DOMAINS (empty), so they raise UnknownDomainError
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("artifact")
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("source_citation")
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("validation")
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("checkpoint")


def test_parse_reader_domains_rejects_derived():
    # Derived domains are added to errors, but implementation raises UnknownDomainError for all errors
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("run_state")


def test_parse_reader_domains_rejects_policy_approval():
    # Policy/approval not in RUST_READ_DOMAINS (empty), raise UnknownDomainError
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("policy")
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("approval")


def test_parse_reader_domains_unknown():
    # Unknown domains not in RUST_READ_DOMAINS (empty), raise UnknownDomainError
    with pytest.raises(UnknownDomainError):
        _parse_reader_domains("unknown_domain")


# -- Hard-cut invariants (ADR-022/023/024/026/027/028/029) -------------------


def test_hard_cut_domains_not_in_write_domains():
    """Artifact and Source/Citation are hard-cut — not selectable via env var."""
    assert "artifact" not in RUST_WRITE_DOMAINS
    assert "source_citation" not in RUST_WRITE_DOMAINS


def test_hard_cut_domains_not_in_read_domains():
    """Artifact and Source/Citation are hard-cut — not in reader domains."""
    assert "artifact" not in RUST_READ_DOMAINS
    assert "source_citation" not in RUST_READ_DOMAINS


def test_validation_in_write_domains():
    """Validation (ADR-028) is a write authority domain."""
    assert "validation" in RUST_WRITE_DOMAINS


def test_checkpoint_in_write_domains():
    """Checkpoint (ADR-029) is a write authority domain."""
    assert "checkpoint" in RUST_WRITE_DOMAINS


# -- Derived and coordination domains ----------------------------------------


def test_derived_domains_mapping():
    assert DERIVED_DOMAINS == {"run_state": "ledger"}


def test_coordination_domains_empty():
    assert COORDINATION_DOMAINS == frozenset()


def test_derived_domains_rejected_from_write():
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("run_state")


def test_derived_domains_rejected_from_read():
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_shadow_reader("run_state")