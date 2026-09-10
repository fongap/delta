"""Tests for packages/storage_authority.py — authority selector contract.

Covers:
- R1 write domains (Idempotency, Ledger, Task Identity — hard-cut via ADR-022/023/024)
- R2 write domains (Artifact ADR-026, Source/Citation ADR-027, Validation ADR-028,
  Checkpoint ADR-029, Policy ADR-030, Approval ADR-031)
- Derived and coordination domains
- Env var parsing rules (fail-fast, no silent ignore)

After R2 Final Convergence (ADR-032), the shadow-reader surface
(``RUST_READ_DOMAINS`` / ``DELTA_RUST_READERS`` / ``is_rust_shadow_reader``)
was deleted — all 6 R2 domains are hard-cut write authorities.
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
    RUST_WRITE_DOMAINS,
    UnknownDomainError,
    _parse_domains,
    is_rust_authority,
)


def os_env() -> str | None:
    """Return the current DELTA_RUST_AUTHORITY value (for _parse_domains tests)."""
    return os.environ.get(ENV_VAR)


# -- R1 write authority domains ----------------------------------------------


def test_rust_write_domains_defined():
    """The R2 write domains that accept DELTA_RUST_AUTHORITY declarations.
    R1 domains (idempotency, ledger, task_identity) are hard-cut (ADR-022/023/024)
    and no longer selectable via env var. Artifact and Source/Citation are also
    hard-cut (ADR-026/027). Validation (ADR-028), Checkpoint (ADR-029),
    Policy (ADR-030), and Approval (ADR-031) are selectable write domains."""
    assert RUST_WRITE_DOMAINS == frozenset({"validation", "checkpoint", "policy", "approval"})


def test_r1_write_rejects_derived_domains(monkeypatch):
    """is_rust_authority must reject derived domains (run_state)."""
    monkeypatch.setenv(ENV_VAR, "run_state")
    with pytest.raises(InvalidAuthorityTargetError):
        is_rust_authority("run_state")


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


# -- R2 write authority domains (hard-cut) -----------------------------------


def test_artifact_is_not_a_selectable_write_domain():
    """ADR-026: artifact is hard-cut to Rust and is no longer a
    selectable authority."""
    assert "artifact" not in RUST_WRITE_DOMAINS


def test_artifact_authority_env_var_is_rejected(monkeypatch):
    """DELTA_RUST_AUTHORITY=artifact must raise (artifact is hard-cut)."""
    monkeypatch.setenv(ENV_VAR, "artifact")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


def test_source_citation_is_hard_cut_not_selectable(monkeypatch):
    """Source/Citation is hard-cut to Rust (ADR-027) - no longer selectable via DELTA_RUST_AUTHORITY."""
    assert "source_citation" not in RUST_WRITE_DOMAINS
    monkeypatch.setenv(ENV_VAR, "source_citation")
    with pytest.raises(UnknownDomainError):
        _parse_domains(os_env())


def test_validation_is_write_authority_domain(monkeypatch):
    assert "validation" in RUST_WRITE_DOMAINS
    monkeypatch.setenv(ENV_VAR, "validation")
    assert is_rust_authority("validation") is True


def test_checkpoint_is_write_authority_domain():
    """Checkpoint is now a write authority (ADR-029)."""
    assert "checkpoint" in RUST_WRITE_DOMAINS


def test_policy_is_write_authority_domain():
    """Policy is now a write authority (ADR-030)."""
    assert "policy" in RUST_WRITE_DOMAINS


def test_approval_is_write_authority_domain():
    """Approval is now a write authority (ADR-031)."""
    assert "approval" in RUST_WRITE_DOMAINS


# -- Domain surface separation -----------------------------------------------


def test_all_domains_is_union():
    """ALL_DOMAINS must be the exact union of write + derived + coordination."""
    expected = RUST_WRITE_DOMAINS | frozenset(DERIVED_DOMAINS) | COORDINATION_DOMAINS
    assert ALL_DOMAINS == expected


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


# -- Hard-cut invariants (ADR-022/023/024/026/027/028/029) -------------------


def test_hard_cut_domains_not_in_write_domains():
    """Artifact and Source/Citation are hard-cut — not selectable via env var."""
    assert "artifact" not in RUST_WRITE_DOMAINS
    assert "source_citation" not in RUST_WRITE_DOMAINS


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