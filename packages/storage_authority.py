"""Storage authority selector — Pre-R1 plumbing (ADR-012).

This module is **plumbing only**, not a switch. It exposes a single
read-only helper :func:`is_rust_authority` that inspects the
``DELTA_RUST_AUTHORITY`` environment variable. The current Python Runtime
ignores this value for write paths; it exists so future R1 authority
switch PRs (PR12+) can gate writes on it without re-plumbing env reads.

R1 status (2026-09-05):

- Python is the only authority. :func:`is_rust_authority` is always
  ``False`` in product builds.
- The CI guard ``scripts/check_rust_authority_migration.py`` reads
  :func:`is_rust_authority` to verify no Python module is writing
  to a Rust-authority domain when the env var is set.
- No PR has yet flipped ``DELTA_RUST_AUTHORITY=1`` in any deployment.
  When the first PR does, the corresponding Python module will gain
  a guard check (``if is_rust_authority(<domain>): return  # Rust is
  authority``) — but the CI guard already enforces that no module
  *unconditionally* writes to a Rust-authority domain.

Contract: see ``docs/architecture/adr/ADR-012-r1-pre-plumbing.md``.
"""

from __future__ import annotations

import os
from typing import Final

#: Environment variable name. Set to ``"1"`` to declare that Rust is the
#: write authority for one or more storage domains. The current Python
#: Runtime respects this only via the CI guard; the runtime itself does
#: not yet refuse writes — that behavior lands per-domain in PR12+.
ENV_VAR: Final[str] = "DELTA_RUST_AUTHORITY"

#: The five storage domains tracked by the authority selector. Each maps
#: to a SQLite DB file currently owned by the Python Runtime.
#: See ADR-011 §提议路径 and ADR-012 §Storage domains.
DOMAINS: Final[tuple[str, ...]] = (
    "idempotency",  # side-effects.db
    "ledger",       # run_events.db
    "run_state",    # run_events.db (subset of ledger for terminal events)
    "task_identity",  # tasks.db (scheduled_tasks + task_runs)
    "storage_transaction",  # abstract; lands in PR16
)


def _truthy(value: str | None) -> bool:
    """True iff ``value`` is one of the truthy env-var spellings."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_rust_authority(domain: str) -> bool:
    """Whether Rust is the declared authority for ``domain``.

    Reads ``DELTA_RUST_AUTHORITY`` once per call (no caching — the env
    var is intended for test/CI scenarios, not hot paths). Returns
    ``False`` if the env var is unset or the domain is not in
    :data:`DOMAINS`.

    Today this always returns ``False`` in product. The CI guard
    ``scripts/check_rust_authority_migration.py`` uses this helper to
    detect the rare scenario where someone sets the env var in CI
    without also gating the corresponding Python write paths.

    :param domain: one of :data:`DOMAINS`.
    :returns: ``True`` if Rust is the declared authority.
    """
    if domain not in DOMAINS:
        return False
    return _truthy(os.environ.get(ENV_VAR))
