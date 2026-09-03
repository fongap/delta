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


# -- CitationRange schema -------------------------------------------------------
# A Source is useful only if a run can be located back to it. The locator shape
# depends on the source kind — text files have lines, PDFs have pages,
# spreadsheets have sheets/rows/cells, chat imports have message ids. We use a
# discriminator (`kind`) so each reader emits the locator shape it actually
# understands and the UI renders the right pointer. `kind: "custom"` is the
# escape hatch for connectors that need to preserve their own descriptor.
KIND_LINES = "lines"
KIND_PAGE = "page"
KIND_CELLS = "cells"
KIND_ROW = "row"
KIND_COLUMN = "column"
KIND_SHEET = "sheet"
KIND_MESSAGE_ID = "message_id"
KIND_CUSTOM = "custom"

_KINDS: tuple[str, ...] = (
    KIND_LINES,
    KIND_PAGE,
    KIND_CELLS,
    KIND_ROW,
    KIND_COLUMN,
    KIND_SHEET,
    KIND_MESSAGE_ID,
    KIND_CUSTOM,
)

# Per-kind allowed fields, used to drop irrelevant entries from the
# canonical-form payload. ``CitationRange`` is intentionally a wide carrier
# (one class describes all kinds), but the persisted shape is the minimum
# needed to render the locator for the chosen kind — extra fields would
# only confuse downstream readers and burn storage.
_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    KIND_LINES: ("start", "end"),
    KIND_PAGE: ("page", "page_end"),
    KIND_CELLS: (
        "sheet",
        "row_start",
        "row_end",
        "col_start",
        "col_end",
        "cell_start",
        "cell_end",
    ),
    KIND_ROW: ("sheet", "row_start", "row_end"),
    KIND_COLUMN: ("sheet", "col_start", "col_end"),
    KIND_SHEET: ("sheet", "row_start", "row_end", "col_start", "col_end"),
    KIND_MESSAGE_ID: ("message_id",),
    KIND_CUSTOM: ("descriptor",),
}


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


@dataclass
class CitationRange:
    """Typed locator for a run's reference into a source.

    Each kind pins the data needed to scroll a viewer back to the exact spot:
    text files use line ranges; PDFs use page numbers; spreadsheets use cell
    ranges with optional sheet/row/column axes; chat connectors use message
    ids; anything else falls under ``custom`` with an opaque descriptor.
    ``to_range_dict`` validates and serializes — invalid input raises
    ``ValueError`` so the run never persists garbage it can't render.
    """

    kind: str
    # Text / log line ranges (1-based, inclusive).
    start: int | None = None
    end: int | None = None
    # Page-based locators (PDF, slide decks).
    page: int | None = None
    page_end: int | None = None
    # Spreadsheet axes (optional sheet/row/column; cell range is row1/row2/col1/col2).
    sheet: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    col_start: int | None = None
    col_end: int | None = None
    cell_start: str | None = None
    cell_end: str | None = None
    # Chat / connector message id.
    message_id: str | None = None
    # Free-form descriptor for ``kind: "custom"``.
    descriptor: dict[str, Any] | None = None


def to_range_dict(value: Any) -> dict[str, Any]:
    """Validate and serialize a CitationRange (or already-dict) into a citation dict.

    Returns the canonical shape to append to ``SourceRef.cited_ranges``. Raises
    ``ValueError`` for an unknown kind, an incomplete shape for the kind, or a
    non-int where one is required — so the run never persists a citation the
    UI cannot render. Only fields relevant to the chosen kind are kept; extra
    fields on a ``CitationRange`` (a wide carrier for ergonomics) and on
    already-dict input are dropped so the persisted shape round-trips.
    """
    if isinstance(value, CitationRange):
        kind = value.kind
        candidate: dict[str, Any] = {
            "start": value.start,
            "end": value.end,
            "page": value.page,
            "page_end": value.page_end,
            "sheet": value.sheet,
            "row_start": value.row_start,
            "row_end": value.row_end,
            "col_start": value.col_start,
            "col_end": value.col_end,
            "cell_start": value.cell_start,
            "cell_end": value.cell_end,
            "message_id": value.message_id,
            "descriptor": value.descriptor,
        }
    elif isinstance(value, dict):
        kind = value.get("kind")
        if not isinstance(kind, str):
            raise ValueError("citation range dict must include a 'kind' string")
        candidate = {k: v for k, v in value.items() if k != "kind"}
    else:
        raise ValueError(
            f"citation range must be a CitationRange or dict, got {type(value).__name__}"
        )

    if kind not in _KINDS:
        raise ValueError(f"unknown citation kind: {kind!r}")

    allowed = _KIND_FIELDS[kind]
    payload: dict[str, Any] = {"kind": kind}
    for field_name in allowed:
        v = candidate.get(field_name)
        if v is not None:
            payload[field_name] = v

    if kind == KIND_LINES:
        if not any(payload.get(k) is not None for k in ("start", "end")):
            raise ValueError("lines citation needs at least one of start/end")
        for k in ("start", "end"):
            v = payload.get(k)
            if v is not None and not isinstance(v, int):
                raise ValueError(f"lines citation {k} must be int, got {type(v).__name__}")
    elif kind == KIND_PAGE:
        if not any(payload.get(k) is not None for k in ("page", "page_end")):
            raise ValueError("page citation needs at least one of page/page_end")
        for k in ("page", "page_end"):
            v = payload.get(k)
            if v is not None and not isinstance(v, int):
                raise ValueError(f"page citation {k} must be int, got {type(v).__name__}")
    elif kind in (KIND_CELLS, KIND_ROW, KIND_COLUMN, KIND_SHEET):
        for k in ("row_start", "row_end", "col_start", "col_end"):
            v = payload.get(k)
            if v is not None and not isinstance(v, int):
                raise ValueError(
                    f"{kind} citation {k} must be int, got {type(v).__name__}"
                )
        if kind == KIND_SHEET and not payload.get("sheet"):
            raise ValueError("sheet citation needs a 'sheet' name")
    elif kind == KIND_MESSAGE_ID:
        if not payload.get("message_id"):
            raise ValueError("message_id citation needs a 'message_id'")
    elif kind == KIND_CUSTOM:
        if not isinstance(payload.get("descriptor"), dict):
            raise ValueError("custom citation needs a 'descriptor' dict")

    return payload


def normalize_cited_ranges(
    ranges: list[Any] | None,
) -> list[dict[str, Any]]:
    """Validate a whole citation list. Same error contract as ``to_range_dict``.

    Empty / None input returns an empty list — not an error, so callers can
    safely call this with no-op citations.
    """
    if not ranges:
        return []
    return [to_range_dict(r) for r in ranges]


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
    def mark_cited(
        self, ref_id: str, run_id: str, ranges: list[Any]
    ) -> bool:
        """Attach a run's citation ranges (pages / rows / message ids) to a source.

        ``ranges`` is a list of :class:`CitationRange` or matching dicts; each
        entry is validated via :func:`normalize_cited_ranges` and stored in
        canonical form. A bad entry raises ``ValueError`` *before* any
        mutation so a partially-applied citation can never be persisted.
        """
        normalized = normalize_cited_ranges(ranges)
        with self._lock:
            ref = self._refs.get(ref_id)
            if ref is None:
                return False
            ref.cited_ranges.append({"run_id": run_id, "ranges": normalized})
            self._save()
            return True

    def add_citation(
        self,
        ref_id: str,
        run_id: str,
        range: CitationRange | dict[str, Any],
    ) -> bool:
        """Single-citation convenience. Same contract as ``mark_cited`` but takes
        one range so readers (PDF, XLSX, ``read_file``) can cite without
        building a list. Validates and appends."""
        return self.mark_cited(ref_id, run_id, [range])

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
    """Contract-side view (SourceDTO) so UI can render provenance without filesystem access.

    Surfaces ``location`` and ``cited_ranges`` (P2 add) so the UI can show
    where a source lives and which runs / ranges have referenced it. Older
    consumers that ignore the new fields keep working because
    ``ContractModel(extra="allow")`` and the optional / defaulted types
    above are forward-compatible.
    """
    from services.server.contracts import SourceDTO

    return SourceDTO(
        id=ref.id,
        origin=ref.origin,
        name=Path(ref.location).name or ref.location,
        fingerprint_prefix=ref.fingerprint[:12],
        freshness=ref.status,
        location=ref.location,
        cited_ranges=list(ref.cited_ranges),
    ).model_dump()
