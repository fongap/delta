"""Artifact — the structured, addressable output of a run (ADR-005 WS2).

Until now `TaskRun.artifacts` was `list[str]` of file paths inferred by mtime
from the workspace folder. The blueprint requires:

  - "Artifact 写入不完整 → 不误报完成" (incomplete writes must not look done)
  - Artifacts must be addressable by id/sha256, not embedded in payloads
  - The Artifact object must be replayable from the run ledger

This module introduces `Artifact` (path, kind, size, sha256, run_id,
incomplete) and a `register_artifact` helper that:

  1. Walks the workspace, computing sha256 for each candidate file
  2. Marks files whose IO fails (truncated, vanished) as `incomplete=True`
  3. Emits an `artifact.registered` ledger event for each new artifact
  4. Emits `artifact.completed` once the sha256 was readable
  5. Reuses the file-kind classifier from `services.server.manager_support`

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

if TYPE_CHECKING:
    from core.ledger import RunEventLedger


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

    Returns None if the file does not exist or cannot be read.
    """
    if kind_classifier is None:
        from services.server.manager_support import _artifact_kind

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
        try:
            ledger.append(
                run_id,
                "artifact.registered",
                actor="system",
                payload=artifact.to_dict(),
                workspace=workspace or None,
            )
            if not artifact.incomplete:
                ledger.append(
                    run_id,
                    "artifact.completed",
                    actor="system",
                    payload={
                        "path": artifact.path,
                        "sha256": artifact.sha256,
                        "size": artifact.size,
                    },
                    workspace=workspace or None,
                )
        except Exception:
            pass
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
        from services.server.manager_support import _artifact_kind

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
            try:
                ledger.append(
                    run_id,
                    "artifact.registered",
                    actor="system",
                    payload=a.to_dict(),
                    workspace=workspace or None,
                )
                if not a.incomplete:
                    ledger.append(
                        run_id,
                        "artifact.completed",
                        actor="system",
                        payload={"path": a.path, "sha256": a.sha256, "size": a.size},
                        workspace=workspace or None,
                    )
            except Exception:
                # The artifact is in the return value; ledger is best-effort.
                pass
    return out
