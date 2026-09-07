"""Storage authority selector — per-domain R1 authority control (ADR-012).

This module parses the ``DELTA_RUST_AUTHORITY`` environment variable
into a set of domain names. Each domain can be independently controlled:

    DELTA_RUST_AUTHORITY=idempotency           # only idempotency → Rust
    DELTA_RUST_AUTHORITY=idempotency,ledger    # two domains → Rust
    DELTA_RUST_AUTHORITY=all                  # all Rust write domains → Rust
    DELTA_RUST_AUTHORITY=1                    # legacy = all (deprecated)
    (unset)                                  # Python (default)

The API :func:`is_rust_authority` returns ``True`` only when the
specified domain is in the parsed set. Callers do not need to know
the env-var parsing rules.

R1.7 (Authority Domain 收口, P0-1):

- The selector distinguishes **Rust write authority** (real Rust
  implementations) from **derived** and **coordination** domains.
- Only the three real Rust write domains accept ``DELTA_RUST_AUTHORITY``
  declarations: ``idempotency``, ``ledger``, ``task_identity``.
- ``run_state`` is derived from ``ledger`` — it is not an independent
  Rust write authority. ``is_rust_authority("run_state")`` raises
  ``ValueError``.
- ``storage_transaction`` is a Python coordination boundary (ADR-015,
  CoreTransaction). It does not accept Rust authority declarations.
- Unknown domains are rejected at config-parse time with ``ValueError``
  — no silent ignoring.
- ``all`` maps only to ``RUST_WRITE_DOMAINS`` (the domains that actually
  have Rust implementations).

Contract: see ``docs/architecture/adr/ADR-012-r1-pre-plumbing.md``.
"""

from __future__ import annotations

import os
from typing import Final

#: Environment variable name. Set to a comma-separated list of domain
#: names, ``"all"``, or a legacy truthy value (``"1"``, ``"true"``,
#: ``"yes"``, ``"on"``) to declare Rust as the write authority for
#: those domains. Unset or empty → Python is the authority.
ENV_VAR: Final[str] = "DELTA_RUST_AUTHORITY"

#: Real Rust write authority domains. These have Rust implementations
#: in core/runtime-native and are the only domains that accept
#: ``DELTA_RUST_AUTHORITY`` declarations.
RUST_WRITE_DOMAINS: Final[frozenset[str]] = frozenset({
    "idempotency",      # side-effects.db
    "ledger",           # run_events.db
    "task_identity",    # tasks.db
})

#: Domains derived from another Rust authority. They do not have
#: independent Rust write authority; they are computed from a Rust
#: write domain.
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
    RUST_WRITE_DOMAINS | frozenset(DERIVED_DOMAINS) | COORDINATION_DOMAINS
)

#: Legacy spelling of ``"all"``. Kept for backward compatibility: maps
#: only to ``RUST_WRITE_DOMAINS``, not to derived or coordination domains.
_LEGACY_ALL: Final[frozenset[str]] = RUST_WRITE_DOMAINS

#: Legacy boolean spellings equivalent to ``"all"`` during the transition
#: period. New deployments should use explicit domain lists or ``"all"``.
_LEGACY_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


class UnknownDomainError(ValueError):
    """Raised when an unrecognized domain name appears in DELTA_RUST_AUTHORITY."""


class InvalidAuthorityTargetError(ValueError):
    """Raised when a non-Rust-write domain is passed to is_rust_authority()."""


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


def is_rust_authority(domain: str) -> bool:
    """Whether Rust is the declared write authority for ``domain``.

    Reads ``DELTA_RUST_AUTHORITY`` once per call (no caching — the env
    var is intended for test/CI scenarios, not hot paths).

    :param domain: one of :data:`RUST_WRITE_DOMAINS`.
    :returns: ``True`` if Rust is the declared write authority.
    :raises InvalidAuthorityTargetError: if ``domain`` is derived
        (``run_state``) or coordination (``storage_transaction``).
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
        raise InvalidAuthorityTargetError(
            f"unknown domain {domain!r}. "
            f"Valid Rust write domains: {sorted(RUST_WRITE_DOMAINS)}"
        )
    return domain in _parse_domains(os.environ.get(ENV_VAR))
