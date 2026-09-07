"""Artifact registry delegate to Rust Core when is_rust_authority is active.

This module is the R2 / PR132 Artifact authority cutover (ADR-020).
When ``DELTA_RUST_AUTHORITY=artifact`` is set, every artifact
registration call is forwarded to the unified ``delta_core`` Rust
process via :class:`DeltaCoreClient` instead of going through the
Python ``core/artifact.py:register_artifact`` / ``register_run_artifacts``
helpers.

The Rust side (PR132):
- :class:`ArtifactRegistryWriter` in ``core/runtime-native/src/artifact.rs``
- New ``artifact.register`` command in the ``delta_core`` protocol
- Reuses the existing ``LedgerWriter`` for hash chain + workspace

The Python side:
- :func:`register_artifact_delegated` replaces direct calls to
  ``core.artifact.register_artifact`` when the delegate is active.
- :func:`register_run_artifacts_delegated` is the parallel wrapper
  for the workspace scanner path.
- :func:`maybe_wrap` is the gating helper: returns a tuple of
  ``(register, register_run)`` callables; callers should use them
  instead of the bare module functions.

Fail-closed (R1.5 P0-3): when ``DELTA_RUST_AUTHORITY=artifact`` is
declared but the ``delta_core`` binary is unavailable, the delegate
raises :class:`DeltaCoreError` — it never silently falls back to
the Python path. Rollback is achieved by unsetting
``DELTA_RUST_AUTHORITY``.

Contract: ``docs/architecture/adr/ADR-020-r2-artifact-authority-switch.md``
and ``core/artifact.py:register_artifact``.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from core.artifact import Artifact, register_artifact, register_run_artifacts

from packages.delta_core_client import DeltaCoreClient, DeltaCoreError, default_client
from packages.storage_authority import is_rust_authority


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the delta_core binary is available."""
    if not is_rust_authority("artifact"):
        return False
    return DeltaCoreClient._find_binary() is not None


def _invoke_artifact_register(
    db_path: str,
    *,
    path: str,
    name: str,
    kind: str,
    size: int,
    modified_at: float,
    run_id: str,
    sha256: str,
    incomplete: bool,
    registered_at: float,
    ts: float,
    workspace: str,
) -> dict[str, Any]:
    """Send one artifact.register command to delta_core."""
    client = default_client()
    return client.command(
        {
            "cmd": "artifact.register",
            "db": db_path,
            "path": path,
            "name": name,
            "kind": kind,
            "size": size,
            "modified_at": modified_at,
            "run_id": run_id,
            "sha256": sha256,
            "incomplete": incomplete,
            "registered_at": registered_at,
            "ts": ts,
            "workspace": workspace,
        }
    )


def register_artifact_delegated(
    workspace: str,
    path: str,
    *,
    run_id: str,
    ledger_db_path: str,
    ledger: Any = None,
    kind_classifier: Callable[..., str] | None = None,
) -> Artifact | None:
    """Register one artifact, routing through Rust when the delegate is active.

    Mirrors :func:`core.artifact.register_artifact` but, when
    ``DELTA_RUST_AUTHORITY=artifact`` is on, the file stat + sha256
    is still computed in Python (the Rust side is currently a pure
    ledger-event writer; it does not re-stat the file), and the
    two ledger events are appended via ``delta_core``.

    Falls back to the Python :func:`register_artifact` when the
    delegate is not active. The Python read path (e.g.
    ``register_artifact`` returning the Artifact object) is preserved
    in both modes.

    The ``ledger`` parameter is used by the Python fallback path to
    write the events to a Python `RunEventLedger` (passed through to
    ``core.artifact.register_artifact``). When the Rust delegate is
    active, ``ledger_db_path`` is used instead (the Rust client opens
    its own connection to the same DB).
    """
    if not _is_delegate_active():
        return register_artifact(
            workspace, path, run_id=run_id,
            ledger=ledger, kind_classifier=kind_classifier,
        )

    # Compute stat + sha256 in Python (Rust is the ledger writer,
    # not the file-walker). This keeps the on-disk semantics
    # identical between modes.
    from pathlib import Path
    root = Path(workspace)
    target = root / path
    if not target.is_file():
        return None
    try:
        stat = target.stat()
    except OSError:
        return None

    if kind_classifier is None:
        from services.server.manager_support import _artifact_kind
        kind_classifier = _artifact_kind

    from core.artifact import _sha256_of
    artifact = Artifact(
        path=path,
        name=target.name,
        kind=kind_classifier(target),
        size=stat.st_size,
        modified_at=stat.st_mtime,
        run_id=run_id,
    )
    try:
        artifact.sha256 = _sha256_of(target)
    except OSError:
        artifact.incomplete = True

    # Append via Rust.
    _invoke_artifact_register(
        ledger_db_path,
        path=artifact.path,
        name=artifact.name,
        kind=artifact.kind,
        size=artifact.size,
        modified_at=artifact.modified_at,
        run_id=artifact.run_id,
        sha256=artifact.sha256 or "",
        incomplete=artifact.incomplete,
        registered_at=artifact.registered_at,
        ts=time.time(),
        workspace=workspace,
    )
    return artifact


def register_run_artifacts_delegated(
    workspace: str,
    *,
    run_id: str,
    since: float,
    ledger_db_path: str,
    kind_classifier: Callable[..., str] | None = None,
    limit: int = 50,
) -> list[Artifact]:
    """Walk + register artifacts, routing each through Rust when active.

    The file scan still happens in Python (R1 pattern: Rust is not
    a file walker). Each artifact is then registered through
    :func:`register_artifact_delegated`, which uses the Rust writer
    when the delegate is active.
    """
    if not _is_delegate_active():
        return register_run_artifacts(
            workspace, run_id=run_id, since=since,
            kind_classifier=kind_classifier, limit=limit,
        )

    from core.artifact import _ARTIFACT_SKIP_DIRS
    from pathlib import Path
    root = Path(workspace)
    if not root.is_dir():
        return []
    if kind_classifier is None:
        from services.server.manager_support import _artifact_kind
        kind_classifier = _artifact_kind

    out: list[Artifact] = []
    from core.artifact import _sha256_of
    for p in root.rglob("*"):
        rel_parts = p.relative_to(root).parts
        if any(part in _ARTIFACT_SKIP_DIRS or part.startswith(".") for part in rel_parts):
            continue
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        if stat.st_mtime < since - 1:
            continue
        rel = str(p.relative_to(root))
        a = Artifact(
            path=rel, name=p.name, kind=kind_classifier(p),
            size=stat.st_size, modified_at=stat.st_mtime, run_id=run_id,
        )
        try:
            a.sha256 = _sha256_of(p)
        except OSError:
            a.incomplete = True
        out.append(a)
        if len(out) >= limit:
            break

    for a in out:
        _invoke_artifact_register(
            ledger_db_path,
            path=a.path, name=a.name, kind=a.kind, size=a.size,
            modified_at=a.modified_at, run_id=a.run_id,
            sha256=a.sha256 or "", incomplete=a.incomplete,
            registered_at=a.registered_at,
            ts=time.time(), workspace=workspace,
        )
    return out


def maybe_wrap(
    ledger_db_path: str,
) -> tuple[Callable[..., Artifact | None], Callable[..., list[Artifact]]]:
    """Return a (register, register_run) pair; delegate when active.

    Fail-closed (R1.5 P0-3): when ``DELTA_RUST_AUTHORITY=artifact`` is
    declared but the ``delta_core`` binary is unavailable, this raises
    :class:`DeltaCoreError` rather than silently falling back to
    the Python path.

    Usage::

        register, register_run = maybe_wrap(str(run_events_db))
        register(workspace, "out/x.md", run_id=run_id, ledger_db_path=str(db))
    """
    if not is_rust_authority("artifact"):
        return (
            lambda workspace, path, *, run_id, **kw: register_artifact(
                workspace, path, run_id=run_id, **kw,
            ),
            lambda workspace, *, run_id, since, **kw: register_run_artifacts(
                workspace, run_id=run_id, since=since, **kw,
            ),
        )
    if not _is_delegate_active():
        raise DeltaCoreError(
            "DELTA_RUST_AUTHORITY declares artifact as a Rust domain "
            "but delta_core binary is not available; refusing to fall "
            "back to Python (fail-closed). Build delta_core or unset "
            "DELTA_RUST_AUTHORITY."
        )

    def _register(
        workspace: str, path: str, *, run_id: str, **kw: Any
    ) -> Artifact | None:
        return register_artifact_delegated(
            workspace, path, run_id=run_id,
            ledger_db_path=ledger_db_path, **kw,
        )

    def _register_run(
        workspace: str, *, run_id: str, since: float, **kw: Any
    ) -> list[Artifact]:
        return register_run_artifacts_delegated(
            workspace, run_id=run_id, since=since,
            ledger_db_path=ledger_db_path, **kw,
        )

    return _register, _register_run
