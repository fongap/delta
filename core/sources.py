"""The source ledger — Sources as first-class citizens (ARCH-001).

A ``SourceRef`` pins one version of an input Delta answered from: a stable id, where it
lives, and the sha256 of the content bytes at capture time. Re-reading the same path
after its content changed produces a *new* fingerprint and flips older refs for that
path to ``changed`` — versioning by fingerprint, never by copying user files.

Freshness is a background check, not a blocking gate: runs record what they saw;
``check_freshness`` re-hashes file-backed locations asynchronously and surfaces drift
(``changed`` / ``missing``) in the ledger for UI/audit to act on.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from packages.jsonstate import load_json_state, save_json_state

ORIGIN_FILE = "file"
ORIGIN_URL = "url"
ORIGIN_CONNECTOR = "connector"
ORIGIN_DB = "db"
ORIGIN_MANUAL = "manual"

FRESH_CURRENT = "current"
FRESH_CHANGED = "changed"
FRESH_MISSING = "missing"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class SourceRef:
    id: str
    origin: str  # file | url | connector | db | manual
    location: str  # workspace-relative path / URI / connector coordinate
    fingerprint: str  # sha256 of content bytes at capture time
    captured_at: str = field(default_factory=_now)
    checked_at: str | None = None
    status: Literal["current", "changed", "missing"] = FRESH_CURRENT
    # Per-run citations ({run_id, ranges}) linking runs → this source.
    cited_ranges: list[dict[str, Any]] = field(default_factory=list)
    # Which sessions/personas may cite it (optional, v1 free-form).
    permissions: dict[str, Any] = field(default_factory=dict)


class SourceStore:
    def __init__(
        self, path: str | Path | None = None, *, workspace: str | Path | None = None
    ) -> None:
        self.path = Path(path) if path else None
        self.workspace = Path(workspace) if workspace else None
        self._lock = threading.Lock()
        self._refs: dict[str, SourceRef] = {}
        self._load()

    # -- persistence ------------------------------------------------------------
    def _load(self) -> None:
        if self.path and self.path.is_file():
            data = load_json_state(self.path, {}) or {}
            for raw in data.get("refs", []):
                ref = SourceRef(**raw)
                self._refs[ref.id] = ref

    def _save(self) -> None:
        if not self.path:
            return
        save_json_state(self.path, {"refs": [asdict(r) for r in self._refs.values()]})

    # -- capturing --------------------------------------------------------------
    def _resolve(self, location: str) -> Path:
        p = Path(location)
        if not p.is_absolute() and self.workspace:
            p = self.workspace / p
        return p

    @staticmethod
    def _location_for(p: Path, workspace: Path | None) -> str:
        """Prefer a workspace-relative, forward-slash path (stable across machines)."""
        if workspace is not None:
            try:
                return p.resolve().relative_to(workspace.resolve()).as_posix()
            except ValueError:
                pass
        return str(p)

    def capture_file(
        self,
        path: str | Path,
        *,
        workspace: str | Path | None = None,
        permissions: dict[str, Any] | None = None,
    ) -> SourceRef:
        """Capture one version of a workspace file: hash its content bytes and record a
        SourceRef. Older refs for the same path flip to ``changed``; re-capturing
        byte-identical content returns the existing ref (no duplicates)."""
        ws = Path(workspace) if workspace else self.workspace
        p = Path(path)
        if not p.is_absolute() and ws:
            p = ws / p
        fingerprint = _sha256(p.read_bytes())
        location = self._location_for(p, ws)
        with self._lock:
            for ref in self._refs.values():
                if ref.origin == ORIGIN_FILE and ref.location == location:
                    if ref.fingerprint == fingerprint:
                        ref.checked_at = _now()
                        ref.status = FRESH_CURRENT
                        self._save()
                        return ref
                    ref.status = FRESH_CHANGED
                    ref.checked_at = _now()
            ref = SourceRef(
                id=uuid.uuid4().hex,
                origin=ORIGIN_FILE,
                location=location,
                fingerprint=fingerprint,
                checked_at=_now(),
                permissions=dict(permissions or {}),
            )
            self._refs[ref.id] = ref
            self._save()
            return ref

    # -- queries ----------------------------------------------------------------
    def get(self, ref_id: str) -> SourceRef | None:
        return self._refs.get(ref_id)

    def list(
        self,
        *,
        origin: str | None = None,
        location: str | None = None,
        status: str | None = None,
    ) -> list[SourceRef]:
        out = list(self._refs.values())
        if origin is not None:
            out = [r for r in out if r.origin == origin]
        if location is not None:
            out = [r for r in out if r.location == location]
        if status is not None:
            out = [r for r in out if r.status == status]
        return sorted(out, key=lambda r: r.captured_at)

    def latest(self, location: str, *, origin: str = ORIGIN_FILE) -> SourceRef | None:
        refs = self.list(origin=origin, location=location)
        return refs[-1] if refs else None

    # -- citations --------------------------------------------------------------
    def mark_cited(self, ref_id: str, run_id: str, ranges: list) -> bool:
        """Attach a run's citation ranges (pages / rows / message ids) to a source."""
        with self._lock:
            ref = self._refs.get(ref_id)
            if ref is None:
                return False
            ref.cited_ranges.append({"run_id": run_id, "ranges": list(ranges)})
            self._save()
            return True

    # -- freshness --------------------------------------------------------------
    def check_freshness(self) -> list[SourceRef]:
        """Re-hash every file-backed ref and surface drift: content differs → ``changed``,
        unreadable → ``missing``, otherwise stay ``current``. Non-file origins are skipped
        in v1 (their revalidation belongs to their connector)."""
        drifted: list[SourceRef] = []
        now = _now()
        with self._lock:
            for ref in self._refs.values():
                if ref.origin != ORIGIN_FILE:
                    continue
                p = self._resolve(ref.location)
                try:
                    matches = _sha256(p.read_bytes()) == ref.fingerprint
                    new_status = FRESH_CURRENT if matches else FRESH_CHANGED
                except OSError:
                    new_status = FRESH_MISSING
                if new_status != FRESH_CURRENT:
                    drifted.append(ref)
                ref.status = new_status
                ref.checked_at = now
            self._save()
        return drifted

    async def check_freshness_async(self) -> list[SourceRef]:
        """The background-check entry point: runs never block on freshness."""
        return await asyncio.to_thread(self.check_freshness)


def to_dto(ref: SourceRef) -> dict[str, Any]:
    """Contract-side view (SourceDTO) so UI can render provenance without filesystem access."""
    from services.server.contracts import SourceDTO

    return SourceDTO(
        id=ref.id,
        origin=ref.origin,
        name=Path(ref.location).name or ref.location,
        fingerprint_prefix=ref.fingerprint[:12],
        freshness=ref.status,
    ).model_dump()
