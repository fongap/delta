"""Storage authority selector — per-domain R1 authority control (ADR-012).

This module parses the ``DELTA_RUST_AUTHORITY`` environment variable
into a set of domain names. Each domain can be independently controlled:

    DELTA_RUST_AUTHORITY=idempotency           # only idempotency → Rust
    DELTA_RUST_AUTHORITY=idempotency,ledger     # two domains → Rust
    DELTA_RUST_AUTHORITY=all                     # all domains → Rust
    DELTA_RUST_AUTHORITY=1                        # legacy = all (deprecated)
    (unset)                                      # Python (default)

The API :func:`is_rust_authority` returns ``True`` only when the
specified domain is in the parsed set. Callers do not need to know
the env-var parsing rules.

R1 status (2026-09-07):

- The delegate wrappers (``maybe_wrap`` / ``maybe_wrap_ledger`` /
  ``maybe_wrap_taskstore``) already gate on :func:`is_rust_authority`.
  With per-domain parsing, a delegate activates only when its specific
  domain is in the set — not when a single global boolean is set.
- The CI guard ``scripts/check_rust_authority_migration.py`` calls
  :func:`is_rust_authority` per domain, so enforcement is also per-domain.
- Legacy boolean spellings (``1``, ``true``, ``yes``, ``on``) are
  equivalent to ``all`` during the transition period. New deployments
  should use explicit domain lists or ``all``.

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

#: The five storage domains tracked by the authority selector. Each maps
#: to a SQLite DB file currently owned by the Python Runtime.
#: See ADR-011 §提议路径 and ADR-012 §Storage domains.
DOMAINS: Final[tuple[str, ...]] = (
    "idempotency",  # side-effects.db
    "ledger",       # run_events.db
    "run_state",    # run_events.db (subset of ledger for terminal events)
    "task_identity",  # tasks.db (scheduled_tasks + task_runs)
    "storage_transaction",  # abstract; lands in R1 final phase
)

_ALL_DOMAINS: Final[frozenset[str]] = frozenset(DOMAINS)

#: Legacy boolean spellings equivalent to ``"all"`` during the transition
#: period. New deployments should use explicit domain lists or ``"all"``.
_LEGACY_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _parse_domains(value: str | None) -> frozenset[str]:
    """Parse the env-var value into a set of domain names.

    - unset / empty → empty set (Python is authority)
    - ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (legacy) → all domains
    - ``"all"`` → all domains
    - ``"idempotency,ledger"`` → ``{idempotency, ledger}``
    - unknown domains → silently ignored
    - whitespace and case → normalized
    """
    if value is None or not value.strip():
        return frozenset()

    raw = value.strip().lower()

    if raw in _LEGACY_TRUTHY or raw == "all":
        return _ALL_DOMAINS

    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return frozenset(p for p in parts if p in _ALL_DOMAINS)


def is_rust_authority(domain: str) -> bool:
    """Whether Rust is the declared authority for ``domain``.

    Reads ``DELTA_RUST_AUTHORITY`` once per call (no caching — the env
    var is intended for test/CI scenarios, not hot paths). Returns
    ``False`` if the env var is unset or the domain is not in
    :data:`DOMAINS`.

    :param domain: one of :data:`DOMAINS`.
    :returns: ``True`` if Rust is the declared authority for this domain.
    """
    if domain not in DOMAINS:
        return False
    return domain in _parse_domains(os.environ.get(ENV_VAR))
