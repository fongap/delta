"""Storage authority selector — per-domain R1 authority control (ADR-012).

This module parses the ``DELTA_RUST_AUTHORITY`` environment variable
into a set of domain names. Each domain can be independently controlled:

    DELTA_RUST_AUTHORITY=ledger                # ledger → Rust
    DELTA_RUST_AUTHORITY=all                  # all Rust write domains → Rust
    DELTA_RUST_AUTHORITY=1                    # legacy = all (deprecated)
    (unset)                                    # remaining domains use Python

Idempotency is no longer selectable: ADR-022 hard-cut it to Rust and removed
the Python writer, fallback, and migration delegate.

The API :func:`is_rust_authority` returns ``True`` only when the
specified domain is in the parsed set. Callers do not need to know
the env-var parsing rules.

R1.7 (Authority Domain 收口, P0-1):

- The selector distinguishes **Rust write authority** (real Rust
  implementations) from **derived** and **coordination** domains.
- Only domains with a live Rust authority path accept
  ``DELTA_RUST_AUTHORITY`` declarations. R2 domains are promoted into this
  set one at a time after their production delegate lands.
- ``run_state`` is derived from ``ledger`` — it is not an independent
  Rust write authority. ``is_rust_authority("run_state")`` raises
  ``ValueError``.
- ``storage_transaction`` is a Python coordination boundary (ADR-015,
  CoreTransaction). It does not accept Rust authority declarations.
- Unknown domains are rejected at config-parse time with ``ValueError``
  — no silent ignoring.
- ``all`` maps only to ``RUST_WRITE_DOMAINS`` (the domains that actually
  have Rust implementations).

R2 (Pre-R2 plumbing, PR131 — ADR-018 / ADR-019):

- A parallel concept **Rust shadow reader** (:data:`RUST_READ_DOMAINS`)
  is introduced for R2 domains whose Rust side currently has only a
  shadow-read implementation (not yet a write authority).
- The API :func:`is_rust_shadow_reader` lets a Python caller ask
  "should I cross-check my work against the Rust reader?" without
  implying that Rust is the write authority.
- R1 write-authority semantics are unchanged: ``is_rust_authority()``
  and ``DELTA_RUST_AUTHORITY`` still cover only the three R1 domains.
- R2 reader enablement is controlled by a separate env var
  :data:`READER_ENV_VAR` (``DELTA_RUST_READERS``) so the two surfaces
  cannot accidentally collide.
- Policy and Approval are **not** in :data:`RUST_READ_DOMAINS` —
  they are evaluation / decision surfaces (not "data with a sha256"),
  so shadow-read is not the right mechanism. They will get a different
  hook in their per-domain ADR.

Contract: see ``docs/architecture/adr/ADR-012-r1-pre-plumbing.md``
(R1) and ``docs/architecture/adr/ADR-019-r2-pre-plumbing.md`` (R2).
"""

from __future__ import annotations

import os
from typing import Final

#: Environment variable name. Set to a comma-separated list of domain
#: names, ``"all"``, or a legacy truthy value (``"1"``, ``"true"``,
#: ``"yes"``, ``"on"``) to declare Rust as the write authority for
#: those domains. Unset or empty → Python is the authority.
ENV_VAR: Final[str] = "DELTA_RUST_AUTHORITY"

#: Environment variable name for R2 shadow-reader enablement. Set to a
#: comma-separated list of R2 reader domains, ``"all"``, or a truthy
#: value to opt into the Rust shadow-read path. Unset or empty → Python
#: is the only reader (no cross-check).
READER_ENV_VAR: Final[str] = "DELTA_RUST_READERS"

#: Real Rust write authority domains. These have Rust implementations
#: in core/runtime-native and are the only domains that accept
#: ``DELTA_RUST_AUTHORITY`` declarations.
RUST_WRITE_DOMAINS: Final[frozenset[str]] = frozenset({
    "artifact",         # run_events.db (artifact.registered + artifact.completed events)
    "source_citation",  # typed citation validity evaluation via delta_core
    "validation",       # deterministic completion gate via delta_core
})

#: Rust shadow-reader domains (R2, ADR-019). These have Rust *readers*
#: (in core/runtime-native) that cross-check Python state but are
#: NOT Rust write authorities yet. Policy and Approval are deliberately
#: excluded — they are evaluation/decision surfaces, not data with a
#: sha256 to verify.
#
# Note (PR132 / ADR-020): ``artifact`` was promoted to
# :data:`RUST_WRITE_DOMAINS` when the Rust write path landed
# (artifact.registered / artifact.completed events). The reader is
# still useful for cross-check but the write path takes precedence.
RUST_READ_DOMAINS: Final[frozenset[str]] = frozenset({
    "checkpoint",       # core/recovery.py — JSON snapshot schema
})

#: Domains derived from another Rust authority. They do not have
#: independent Rust write authority; they are computed from a Rust
#: write domain.
#:
#: Note (ADR-023): ``run_state`` was previously derived from ``ledger``.
#: After the ledger hard-cut, ``RunEventLedger`` is a thin Rust facade
#: and ``run_state`` is computed directly from it.  The derivation
#: entry is retained for backward compatibility but ``is_rust_authority``
#: still raises for derived domains.
DERIVED_DOMAINS: Final[dict[str, str]] = {
    "run_state": "ledger",
}

#: Python coordination boundaries. These coordinate across domains but
#: do not have Rust write authority. They accept no Rust declarations.
COORDINATION_DOMAINS: Final[frozenset[str]] = frozenset({
    "storage_transaction",
})

#: All domain names recognized by the selector. Derived and coordination
#: domains are NOT valid targets for is_rust_authority().
ALL_DOMAINS: Final[frozenset[str]] = (
    RUST_WRITE_DOMAINS
    | RUST_READ_DOMAINS
    | frozenset(DERIVED_DOMAINS)
    | COORDINATION_DOMAINS
)

#: Legacy spelling of ``"all"``. Kept for backward compatibility: maps
#: only to ``RUST_WRITE_DOMAINS``, not to derived or coordination domains.
_LEGACY_ALL: Final[frozenset[str]] = RUST_WRITE_DOMAINS

#: Legacy boolean spellings equivalent to ``"all"`` during the transition
#: period. New deployments should use explicit domain lists or ``"all"``.
_LEGACY_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


class UnknownDomainError(ValueError):
    """Raised when an unrecognized domain name appears in DELTA_RUST_AUTHORITY
    or DELTA_RUST_READERS."""


class InvalidAuthorityTargetError(ValueError):
    """Raised when a non-Rust-write domain is passed to is_rust_authority()
    or a non-Rust-reader domain is passed to is_rust_shadow_reader()."""


def _parse_domains(value: str | None) -> frozenset[str]:
    """Parse the env-var value into a set of domain names.

    - unset / empty → empty set (Python is authority)
    - ``"1"``, ``"true"``, ``"yes"``, ``"on"`` → ``RUST_WRITE_DOMAINS``
    - ``"all"`` → ``RUST_WRITE_DOMAINS``
    - ``"idempotency,ledger"`` → ``{idempotency, ledger}``
    - ``"run_state"`` / ``"storage_transaction"`` → ``ValueError``
      (these are derived/coordination, not Rust write authority)
    - unknown domains → ``ValueError`` (fail-fast, no silent ignore)
    - whitespace and case → normalized

    :raises UnknownDomainError: if any token is not in ``ALL_DOMAINS``.
    :raises InvalidAuthorityTargetError: if ``run_state`` or
        ``storage_transaction`` appears in the list.
    """
    if value is None or not value.strip():
        return frozenset()

    raw = value.strip().lower()

    if raw in _LEGACY_TRUTHY or raw == "all":
        return _LEGACY_ALL

    parts: set[str] = set()
    errors: list[str] = []

    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if part in DERIVED_DOMAINS:
            errors.append(
                f"{part!r} is derived from {DERIVED_DOMAINS[part]!r}, "
                f"not a Rust write authority domain"
            )
        elif part in COORDINATION_DOMAINS:
            errors.append(
                f"{part!r} is a Python coordination boundary, "
                f"not a Rust write authority domain"
            )
        elif part not in RUST_WRITE_DOMAINS:
            errors.append(f"unknown domain {part!r}")
        else:
            parts.add(part)

    if errors:
        raise UnknownDomainError(
            f"{ENV_VAR} contains invalid entries: "
            + "; ".join(errors)
            + f". Valid Rust write domains: {sorted(RUST_WRITE_DOMAINS)}"
        )

    return frozenset(parts)


def _parse_reader_domains(value: str | None) -> frozenset[str]:
    """Parse the R2 reader env-var value into a set of domain names.

    - unset / empty → empty set (no Rust reader; Python is sole reader)
    - ``"1"`` / ``"true"`` / ``"yes"`` / ``"on"`` → ``RUST_READ_DOMAINS``
    - ``"all"`` → ``RUST_READ_DOMAINS``
    - ``"artifact,validation"`` → ``{artifact, validation}``
    - R1 write domains (idempotency / ledger / task_identity) → error
      (use DELTA_RUST_AUTHORITY for write authority)
    - ``run_state`` / ``storage_transaction`` → error
    - ``policy`` / ``approval`` → error
      (these are evaluation/decision surfaces, not data readers;
      they will get a different hook in their per-domain ADR)
    - unknown domains → ``ValueError`` (fail-fast, no silent ignore)
    """
    if value is None or not value.strip():
        return frozenset()

    raw = value.strip().lower()

    if raw in _LEGACY_TRUTHY or raw == "all":
        return RUST_READ_DOMAINS

    parts: set[str] = set()
    errors: list[str] = []

    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if part in RUST_WRITE_DOMAINS:
            errors.append(
                f"{part!r} is a Rust WRITE domain; use {ENV_VAR} "
                f"for write authority, not {READER_ENV_VAR}"
            )
        elif part in DERIVED_DOMAINS:
            errors.append(
                f"{part!r} is derived from {DERIVED_DOMAINS[part]!r}, "
                f"not a Rust shadow-reader domain"
            )
        elif part in COORDINATION_DOMAINS:
            errors.append(
                f"{part!r} is a Python coordination boundary, "
                f"not a Rust shadow-reader domain"
            )
        elif part not in RUST_READ_DOMAINS:
            errors.append(
                f"unknown R2 reader domain {part!r}. "
                f"Valid R2 reader domains: {sorted(RUST_READ_DOMAINS)}"
            )
        else:
            parts.add(part)

    if errors:
        raise UnknownDomainError(
            f"{READER_ENV_VAR} contains invalid entries: "
            + "; ".join(errors)
            + f". Valid R2 reader domains: {sorted(RUST_READ_DOMAINS)}"
        )

    return frozenset(parts)


def is_rust_authority(domain: str) -> bool:
    """Whether Rust is the declared write authority for ``domain``.

    Reads ``DELTA_RUST_AUTHORITY`` once per call (no caching — the env
    var is intended for test/CI scenarios, not hot paths).

    :param domain: one of :data:`RUST_WRITE_DOMAINS`.
    :returns: ``True`` if Rust is the declared write authority.
    :raises InvalidAuthorityTargetError: if ``domain`` is derived
        (``run_state``), coordination (``storage_transaction``), an R2
        reader domain (``artifact`` / ``validation`` / ``checkpoint`` /
        ``checkpoint``), or unknown.
    """
    if domain not in RUST_WRITE_DOMAINS:
        if domain in DERIVED_DOMAINS:
            source = DERIVED_DOMAINS[domain]
            raise InvalidAuthorityTargetError(
                f"{domain!r} is derived from {source!r}, "
                f"not a Rust write authority domain. "
                f"Use is_rust_authority({source!r})."
            )
        if domain in COORDINATION_DOMAINS:
            raise InvalidAuthorityTargetError(
                f"{domain!r} is a Python coordination boundary, "
                f"not a Rust write authority domain."
            )
        if domain in RUST_READ_DOMAINS:
            raise InvalidAuthorityTargetError(
                f"{domain!r} is an R2 shadow-reader domain. "
                f"Rust has a reader but no write authority for it yet. "
                f"Use is_rust_shadow_reader({domain!r}) to query the reader."
            )
        raise InvalidAuthorityTargetError(
            f"unknown domain {domain!r}. "
            f"Valid Rust write domains: {sorted(RUST_WRITE_DOMAINS)}"
        )
    return domain in _parse_domains(os.environ.get(ENV_VAR))


def is_rust_shadow_reader(domain: str) -> bool:
    """Whether the Rust shadow reader is enabled for ``domain`` (R2, ADR-019).

    The shadow reader is the Rust implementation that reads the same
    state (ledger events, file sha256, JSON snapshots, citation ranges)
    as the Python authority and cross-checks it. It is **not** a write
    authority — the Python code is still the only writer.

    Reads ``DELTA_RUST_READERS`` once per call (no caching — the env
    var is intended for test/CI scenarios, not hot paths).

    :param domain: one of :data:`RUST_READ_DOMAINS`
        (``validation`` / ``checkpoint``).
    :returns: ``True`` if the Rust reader is enabled.
    :raises InvalidAuthorityTargetError: if ``domain`` is an R1 write
        domain, derived, coordination, ``policy`` / ``approval``, or
        unknown.
    """
    if domain not in RUST_READ_DOMAINS:
        if domain in RUST_WRITE_DOMAINS:
            raise InvalidAuthorityTargetError(
                f"{domain!r} is a Rust WRITE domain. "
                f"Use is_rust_authority({domain!r}) for write authority."
            )
        if domain in DERIVED_DOMAINS:
            source = DERIVED_DOMAINS[domain]
            raise InvalidAuthorityTargetError(
                f"{domain!r} is derived from {source!r}, "
                f"not an R2 shadow-reader domain."
            )
        if domain in COORDINATION_DOMAINS:
            raise InvalidAuthorityTargetError(
                f"{domain!r} is a Python coordination boundary, "
                f"not an R2 shadow-reader domain."
            )
        raise InvalidAuthorityTargetError(
            f"unknown R2 reader domain {domain!r}. "
            f"Valid R2 reader domains: {sorted(RUST_READ_DOMAINS)}"
        )
    return domain in _parse_reader_domains(os.environ.get(READER_ENV_VAR))
