"""Artifact — the structured, addressable output of a run (ADR-005 WS2).

Until now `TaskRun.artifacts` was `list[str]` of file paths inferred by mtime
from the workspace folder. The blueprint requires:

  - "Artifact 写入不完整 → 不误报完成" (incomplete writes must not look done)
  - Artifacts must be addressable by id/sha256, not embedded in payloads
  - The Artifact object must be replayable from the run ledger

This module defines `Artifact` (path, kind, size, sha256, run_id,
incomplete) and the discovery surface: `register_artifact` and
`register_run_artifacts` scan the workspace, stat each candidate, compute
its sha256, and classify its kind.

The R2 Artifact Registry Hard-Cut (ADR-026) makes Rust ``delta_core`` the
**sole** authority for artifact facts.  This module only performs file
discovery and candidate construction:

  1. Walks the workspace, computing sha256 for each candidate file
  2. Marks files whose IO fails (truncated, vanished) as `incomplete=True`
  3. Sends each candidate to ``delta_core`` via the single
     ``artifact.register`` command (through
     :class:`~packages.delta_core_client.DeltaCoreClient`), which appends
     the ``artifact.registered`` / ``artifact.completed`` ledger events.

Python never writes artifact facts or artifact ledger events directly.
Registration is fail-closed: if ``delta_core`` is unavailable or returns
a malformed response, :class:`~packages.delta_core_client.DeltaCoreError`
is raised (no Python fallback).  File-level errors (missing path, stat
OSError, hash OSError) remain non-fatal and surface as `None` or
`incomplete=True`, exactly as before.

The old `list[str]` `TaskRun.artifacts` field is now `list[Artifact]`. The
field is forward-compatible: `Artifact.to_dict()` and `Artifact.from_dict()`
mirror the wire shape used by `ArtifactDTO` in `services/server/contracts.py`,
so REST/UI consumers can switch in place.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from packages.delta_core_client import default_client

if TYPE_CHECKING:
    from core.ledger import RunEventLedger


def _artifact_kind(path: Path) -> str:
    """Classify an artifact's kind by file suffix (R6: moved in from the
    removed `services.server.manager_support` so core/ has no server coupling)."""
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".xlsx", ".xls"}:
        return "sheet"
    if suffix in {".pptx", ".ppt", ".pptm", ".docx", ".doc", ".docm"}:
        return "office"
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix in {".py", ".js", ".ts", ".tsx", ".css", ".json"}:
        return "code"
    return "text"


@dataclass
class Artifact:
    """A run-produced output. Addressable by path+run_id; verifiable by sha256."""

    path: str  # workspace-relative
    name: str
    kind: str  # "markdown" | "csv" | "sheet" | "image" | "code" | ...
    size: int
    modified_at: float
    run_id: str
    sha256: str | None = None
    incomplete: bool = False  # True ⇒ write may be truncated; do not consume
    registered_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "modified_at": self.modified_at,
            "run_id": self.run_id,
            "sha256": self.sha256,
            "incomplete": self.incomplete,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Artifact":
        return cls(
            path=d["path"],
            name=d["name"],
            kind=d.get("kind", "text"),
            size=int(d.get("size", 0)),
            modified_at=float(d.get("modified_at", 0.0)),
            run_id=d.get("run_id", ""),
            sha256=d.get("sha256"),
            incomplete=bool(d.get("incomplete", False)),
            registered_at=float(d.get("registered_at", 0.0)),
        )


_ARTIFACT_SKIP_DIRS = (".delta", ".git", "__pycache__", "node_modules")


def _sha256_of(path: Path, *, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _register_via_rust(
    artifact: Artifact,
    *,
    db_path: str,
    workspace: str,
) -> None:
    """Send one ``artifact.register`` command to Rust.

    Rust ``delta_core`` is the sole authority for artifact facts: it
    appends ``artifact.registered`` and (if not incomplete)
    ``artifact.completed`` to the run ledger.  This call is fail-closed
    — any protocol error, malformed response, or unavailable binary
    surfaces as :class:`DeltaCoreError` and is not swallowed.
    """
    default_client().command(
        {
            "cmd": "artifact.register",
            "db": db_path,
            "path": artifact.path,
            "name": artifact.name,
            "kind": artifact.kind,
            "size": artifact.size,
            "modified_at": artifact.modified_at,
            "run_id": artifact.run_id,
            "sha256": artifact.sha256 or "",
            "incomplete": artifact.incomplete,
            "registered_at": artifact.registered_at,
            "workspace": workspace or "",
        }
    )


def register_artifact(
    workspace: str,
    path: str,
    *,
    run_id: str,
    ledger: "RunEventLedger | None" = None,
    kind_classifier: Callable[[Path], str] | None = None,
) -> Artifact | None:
    """Explicitly register a single file as an artifact.

    This is the **new main path** (P1-D): write tools that know they
    produced a file call this after a successful write, so the artifact
    is registered immediately (not waiting for the post-run mtime scan).
    The workspace scanner remains as a fallback/reconciliation layer.

    The file is discovered/classified here in Python; the resulting fact
    is persisted by Rust ``delta_core`` (ADR-026).  When ``ledger`` is
    provided it supplies the run-events DB path (and therefore the Rust
    write target).  When ``ledger`` is ``None`` the artifact is computed
    but no fact is persisted (matches the historical "no ledger" path).

    Returns None if the file does not exist or cannot be read.  Raises
    :class:`DeltaCoreError` if Rust registration fails.
    """
    if kind_classifier is None:
        kind_classifier = _artifact_kind

    root = Path(workspace)
    target = root / path
    if not target.is_file():
        return None
    try:
        stat = target.stat()
    except OSError:
        return None
    artifact = Artifact(
        path=path,
        name=Path(path).name,
        kind=kind_classifier(target),
        size=stat.st_size,
        modified_at=stat.st_mtime,
        run_id=run_id,
    )
    try:
        artifact.sha256 = _sha256_of(target)
    except OSError:
        artifact.incomplete = True
    if ledger is not None:
        _register_via_rust(artifact, db_path=str(ledger.db_path), workspace=workspace)
    return artifact


def register_run_artifacts(
    workspace: str,
    *,
    run_id: str,
    since: float,
    ledger: "RunEventLedger | None" = None,
    kind_classifier: Callable[[Path], str] | None = None,
    limit: int = 50,
) -> list[Artifact]:
    """Walk `workspace` and register every file modified after `since` as an artifact.

    `kind_classifier` defaults to the suffix-based classifier from
    `services.server.manager_support`; tests inject a deterministic stub.
    Files that fail to read (incomplete writes) are registered with
    `incomplete=True` and an empty sha256, so the run can be reported as
    "artifact present but not yet readable" instead of silently missing.
    """
    if kind_classifier is None:
        kind_classifier = _artifact_kind

    root = Path(workspace)
    out: list[Artifact] = []
    if not root.is_dir():
        return out

    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if any(part in _ARTIFACT_SKIP_DIRS or part.startswith(".") for part in rel_parts):
            continue
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < since - 1:  # 1s slack for filesystem mtime granularity
            continue

        rel = str(path.relative_to(root))
        artifact = Artifact(
            path=rel,
            name=path.name,
            kind=kind_classifier(path),
            size=stat.st_size,
            modified_at=stat.st_mtime,
            run_id=run_id,
        )
        try:
            artifact.sha256 = _sha256_of(path)
        except OSError:
            artifact.incomplete = True
        out.append(artifact)
        if len(out) >= limit:
            break

    if ledger is not None:
        for a in out:
            _register_via_rust(a, db_path=str(ledger.db_path), workspace=workspace)
    return out
