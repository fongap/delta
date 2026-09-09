"""The source ledger — Sources as first-class citizens (ARCH-001).

A ``SourceRef`` pins one version of an input Delta answered from: a stable id, where it
lives, and the sha256 of the content bytes at capture time. Re-reading the same path
after its content changed produces a *new* fingerprint and flips older refs for that
path to ``changed`` — versioning by fingerprint, never by copying user files.

Freshness is a background check, not a blocking gate: runs record what they saw;
``check_freshness`` re-hashes file-backed locations asynchronously and surfaces drift
(``changed`` / ``missing``) in the ledger for UI/audit to act on.

R2 Hard-Cut (ADR-027): Rust ``delta_core`` is the **sole** authority for Source /
Citation trusted facts. This module is a thin Python facade that performs
file I/O and fingerprint computation (extraction), then delegates all
persistence and final verdicts to Rust via :class:`~packages.delta_core_client.DeltaCoreClient`.

Python responsibilities:
- Read file bytes, compute sha256, stat for mtime/size
- Construct candidate range dicts from :class:`CitationRange`

Rust responsibilities (via ``delta_core``):
- Source identity, revision, and fingerprint facts
- Citation marking and range validation
- Stale detection and status transitions
- All trusted persistence in the run-event ledger
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from packages.delta_core_client import DeltaCoreError, default_client

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
    # mtime / size at the moment the fingerprint was last confirmed.
    # Used as a cheap pre-check before hashing the file again (P3 §7.3
    # Source 索引失效 — mtime + size unchanged means content is
    # unchanged, no need to re-sha256). ``None`` for non-file origins
    # and for refs captured by older code that didn't record them.
    mtime_ns: int | None = None
    size_bytes: int | None = None
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

    This function is a **candidate construction helper**. The authoritative
    range validation and normalization is performed by Rust ``delta_core``
    during ``citation.mark``.
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
            if v is not None and type(v) is not int:
                raise ValueError(f"lines citation {k} must be int, got {type(v).__name__}")
            if v is not None and v < 1:
                raise ValueError(f"lines citation {k} must be >= 1, got {v}")
        start = payload.get("start", payload.get("end"))
        end = payload.get("end", payload.get("start"))
        assert start is not None and end is not None
        if end < start:
            raise ValueError(f"lines citation end ({end}) < start ({start})")
    elif kind == KIND_PAGE:
        if not any(payload.get(k) is not None for k in ("page", "page_end")):
            raise ValueError("page citation needs at least one of page/page_end")
        for k in ("page", "page_end"):
            v = payload.get(k)
            if v is not None and type(v) is not int:
                raise ValueError(f"page citation {k} must be int, got {type(v).__name__}")
            if v is not None and v < 1:
                raise ValueError(f"page citation {k} must be >= 1, got {v}")
        page = payload.get("page", payload.get("page_end"))
        page_end = payload.get("page_end", payload.get("page"))
        assert page is not None and page_end is not None
        if page_end < page:
            raise ValueError(
                f"page citation page_end ({page_end}) < page ({page})"
            )
    elif kind in (KIND_CELLS, KIND_ROW, KIND_COLUMN, KIND_SHEET):
        sheet = payload.get("sheet")
        if not isinstance(sheet, str) or not sheet:
            raise ValueError(f"{kind} citation needs a 'sheet' name")
        for k in ("row_start", "row_end", "col_start", "col_end"):
            v = payload.get(k)
            if v is not None and type(v) is not int:
                raise ValueError(
                    f"{kind} citation {k} must be int, got {type(v).__name__}"
                )
            if v is not None and v < 1:
                raise ValueError(f"{kind} citation {k} must be >= 1, got {v}")
        for start_name, end_name in (
            ("row_start", "row_end"),
            ("col_start", "col_end"),
        ):
            start = payload.get(start_name)
            end = payload.get(end_name)
            if start is not None and end is not None and end < start:
                raise ValueError(
                    f"{kind} citation {end_name} ({end}) < {start_name} ({start})"
                )
        if kind == KIND_CELLS:
            numeric_locator = any(
                payload.get(k) is not None
                for k in ("row_start", "row_end", "col_start", "col_end")
            )
            a1_locator = any(
                payload.get(k) is not None for k in ("cell_start", "cell_end")
            )
            if not numeric_locator and not a1_locator:
                raise ValueError("cells citation needs a cell or row/column range")
            for k in ("cell_start", "cell_end"):
                v = payload.get(k)
                if v is not None and (not isinstance(v, str) or not v):
                    raise ValueError(f"cells citation {k} must be a non-empty string")
        elif kind == KIND_ROW and not any(
            payload.get(k) is not None for k in ("row_start", "row_end")
        ):
            raise ValueError("row citation needs at least one of row_start/row_end")
        elif kind == KIND_COLUMN and not any(
            payload.get(k) is not None for k in ("col_start", "col_end")
        ):
            raise ValueError("column citation needs at least one of col_start/col_end")
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

    This function is a **candidate construction helper**. The authoritative
    range validation and normalization is performed by Rust ``delta_core``
    during ``citation.mark``.
    """
    if not ranges:
        return []
    return [to_range_dict(r) for r in ranges]


class SourceStore:
    """Thin facade over the Rust ``delta_core`` Source/Citation authority.

    All trusted Source/Citation facts are persisted in the run-event ledger
    (``run_events.db``) by the Rust process. This class performs file I/O
    and fingerprint computation locally, then delegates to Rust via the
    :class:`~packages.delta_core_client.DeltaCoreClient`.

    The constructor takes the ledger database path (``run_events.db``) and
    the workspace root. The legacy ``sources.json`` file is no longer used.
    """

    def __init__(
        self, db_path: str | Path | None = None, *, workspace: str | Path | None = None
    ) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.workspace = Path(workspace) if workspace else None

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

    def _require_db(self) -> str:
        if not self.db_path:
            raise DeltaCoreError("SourceStore requires a ledger db_path")
        return str(self.db_path)

    def _send_command(self, cmd: dict[str, Any]) -> Any:
        """Send a command to delta_core, raising DeltaCoreError on failure."""
        return default_client().command(cmd)

    # -- capturing --------------------------------------------------------------
    def capture_file(
        self,
        path: str | Path,
        *,
        workspace: str | Path | None = None,
        permissions: dict[str, Any] | None = None,
    ) -> SourceRef:
        """Capture one version of a workspace file: hash its content bytes and register
        the source via Rust. Older refs for the same path flip to ``changed``;
        re-capturing byte-identical content returns the existing ref (no duplicates).
        """
        ws = Path(workspace) if workspace else self.workspace
        p = Path(path)
        if not p.is_absolute() and ws:
            p = ws / p
        data = p.read_bytes()
        fingerprint = _sha256(data)
        location = self._location_for(p, ws)
        # mtime / size at capture time — used as a cheap pre-check
        # in the freshness index (P3 §7.3 Source 索引失效). A future
        # check that sees the same (mtime_ns, size_bytes) doesn't need
        # to re-hash the file to know nothing changed.
        mtime_ns: int | None = None
        size_bytes: int | None = None
        try:
            st = p.stat()
            mtime_ns = getattr(st, "st_mtime_ns", None) or int(st.st_mtime * 1_000_000_000)
            size_bytes = st.st_size
        except OSError:
            pass

        captured_at = _now()
        result = self._send_command({
            "cmd": "source.register",
            "db": self._require_db(),
            "origin": ORIGIN_FILE,
            "location": location,
            "fingerprint": fingerprint,
            "captured_at": captured_at,
            "mtime_ns": mtime_ns,
            "size_bytes": size_bytes,
            "permissions": permissions or {},
            "run_id": "",  # source events use $source namespace
            "ts": time.time(),
            "workspace": str(ws) if ws else "",
        })

        # Convert Rust response to SourceRef
        return SourceRef(
            id=result["id"],
            origin=result["origin"],
            location=result["location"],
            fingerprint=result["fingerprint"],
            captured_at=result["captured_at"],
            checked_at=result.get("checked_at"),
            status=result["status"],
            mtime_ns=result.get("mtime_ns"),
            size_bytes=result.get("size_bytes"),
            cited_ranges=result.get("cited_ranges", []),
            permissions=result.get("permissions", {}),
        )

    # -- queries ----------------------------------------------------------------
    def get(self, ref_id: str) -> SourceRef | None:
        result = self._send_command({
            "cmd": "source.get",
            "db": self._require_db(),
            "source_id": ref_id,
        })
        if result is None:
            return None
        return SourceRef(
            id=result["id"],
            origin=result["origin"],
            location=result["location"],
            fingerprint=result["fingerprint"],
            captured_at=result["captured_at"],
            checked_at=result.get("checked_at"),
            status=result["status"],
            mtime_ns=result.get("mtime_ns"),
            size_bytes=result.get("size_bytes"),
            cited_ranges=result.get("cited_ranges", []),
            permissions=result.get("permissions", {}),
        )

    def list(
        self,
        *,
        origin: str | None = None,
        location: str | None = None,
        status: str | None = None,
    ) -> list[SourceRef]:
        result = self._send_command({
            "cmd": "source.list",
            "db": self._require_db(),
            "origin": origin,
            "location": location,
            "status": status,
        })
        if not isinstance(result, list):
            return []
        return [
            SourceRef(
                id=r["id"],
                origin=r["origin"],
                location=r["location"],
                fingerprint=r["fingerprint"],
                captured_at=r["captured_at"],
                checked_at=r.get("checked_at"),
                status=r["status"],
                mtime_ns=r.get("mtime_ns"),
                size_bytes=r.get("size_bytes"),
                cited_ranges=r.get("cited_ranges", []),
                permissions=r.get("permissions", {}),
            )
            for r in result
        ]

    def latest(self, location: str, *, origin: str = ORIGIN_FILE) -> SourceRef | None:
        result = self._send_command({
            "cmd": "source.latest",
            "db": self._require_db(),
            "location": location,
            "origin": origin,
        })
        if result is None:
            return None
        return SourceRef(
            id=result["id"],
            origin=result["origin"],
            location=result["location"],
            fingerprint=result["fingerprint"],
            captured_at=result["captured_at"],
            checked_at=result.get("checked_at"),
            status=result["status"],
            mtime_ns=result.get("mtime_ns"),
            size_bytes=result.get("size_bytes"),
            cited_ranges=result.get("cited_ranges", []),
            permissions=result.get("permissions", {}),
        )

    # -- citations --------------------------------------------------------------
    def mark_cited(
        self, ref_id: str, run_id: str, ranges: list[Any]
    ) -> bool:
        """Attach a run's citation ranges (pages / rows / message ids) to a source.

        ``ranges`` is a list of :class:`CitationRange` or matching dicts; each
        entry is validated by Rust ``delta_core`` and stored in canonical form.
        A bad entry raises ``DeltaCoreError`` (fail-closed) — no Python fallback.
        """
        # Convert CitationRange objects to dicts for the wire protocol
        wire_ranges: list[dict[str, Any]] = []
        for value in ranges:
            if isinstance(value, CitationRange):
                d = {
                    "kind": value.kind,
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
                # Drop None values to keep payload minimal
                wire_ranges.append({k: v for k, v in d.items() if v is not None})
            else:
                wire_ranges.append(value)

        result = self._send_command({
            "cmd": "citation.mark",
            "db": self._require_db(),
            "source_id": ref_id,
            "run_id": run_id,
            "ranges": wire_ranges,
            "ts": time.time(),
            "workspace": str(self.workspace) if self.workspace else "",
        })
        return bool(result.get("marked", False))

    def add_citation(
        self,
        ref_id: str,
        run_id: str,
        range: CitationRange | dict[str, Any],
    ) -> bool:
        """Single-citation convenience. Same contract as ``mark_cited`` but takes
        one range so readers (PDF, XLSX, ``read_file``) can cite without
        building a list. Validates and appends via Rust."""
        return self.mark_cited(ref_id, run_id, [range])

    # -- freshness --------------------------------------------------------------
    def check_freshness(self) -> list[SourceRef]:
        """Re-check every file-backed ref and surface drift: content differs → ``changed``,
        unreadable → ``missing``, otherwise stay ``current``. Non-file origins are skipped
        in v1 (their revalidation belongs to their connector).

        P3 §7.3 Source 索引失效: the implementation now uses an mtime +
        size fast path. When the on-disk (mtime_ns, size_bytes) matches
        the ref's last-seen values we know the content hasn't changed
        and skip the sha256 entirely — the same content-currency
        guarantee at a tiny fraction of the cost for large files.

        This method performs the file I/O and hash computation locally,
        then sends the observations to Rust for the final status verdict.
        """
        refs = self.list()
        checks = []
        for ref in refs:
            if ref.origin != ORIGIN_FILE:
                continue
            p = self._resolve(ref.location)
            try:
                st = p.stat()
                current_mtime_ns = getattr(st, "st_mtime_ns", None) or int(
                    st.st_mtime * 1_000_000_000
                )
                current_size = st.st_size
                if (
                    ref.mtime_ns is not None
                    and ref.size_bytes is not None
                    and ref.mtime_ns == current_mtime_ns
                    and ref.size_bytes == current_size
                ):
                    # Fast path: mtime+size unchanged, no need to re-hash
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": ref.fingerprint,
                        "mtime_ns": current_mtime_ns,
                        "size_bytes": current_size,
                    })
                    continue
                # Mtime/size differ: re-hash and send observation
                try:
                    data = p.read_bytes()
                    current_fp = _sha256(data)
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": current_fp,
                        "mtime_ns": current_mtime_ns,
                        "size_bytes": current_size,
                    })
                except OSError:
                    # File unreadable
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": None,
                        "mtime_ns": None,
                        "size_bytes": None,
                    })
            except OSError:
                # File missing
                checks.append({
                    "source_id": ref.id,
                    "fingerprint": None,
                    "mtime_ns": None,
                    "size_bytes": None,
                })

        if not checks:
            return []

        result = self._send_command({
            "cmd": "source.refresh",
            "db": self._require_db(),
            "checks": checks,
            "ts": time.time(),
            "workspace": str(self.workspace) if self.workspace else "",
        })

        # Convert drifted sources back to SourceRef
        drifted: list[SourceRef] = []
        if isinstance(result, list):
            for r in result:
                drifted.append(SourceRef(
                    id=r["id"],
                    origin=r["origin"],
                    location=r["location"],
                    fingerprint=r["fingerprint"],
                    captured_at=r["captured_at"],
                    checked_at=r.get("checked_at"),
                    status=r["status"],
                    mtime_ns=r.get("mtime_ns"),
                    size_bytes=r.get("size_bytes"),
                    cited_ranges=r.get("cited_ranges", []),
                    permissions=r.get("permissions", {}),
                ))
        return drifted

    async def check_freshness_async(self) -> list[SourceRef]:
        """The background-check entry point: runs never block on freshness."""
        return await asyncio.to_thread(self.check_freshness)

    def reindex_stale(self, force: bool = False) -> list[SourceRef]:
        """Targeted freshness re-check for the refs that have actually
        drifted (P3 §7.3 Source 索引失效).

        Walks every file-backed ref and uses the mtime fast path; only
        refs whose (mtime, size) shifted OR whose ``status`` is not
        ``current`` get a sha256 check. ``force=True`` skips the
        mtime optimization and rehashes everything (use after a
        restore / git checkout that rewrote files with their original
        mtimes).

        Returns the list of refs whose status flipped to
        ``changed`` or ``missing`` in this pass. Refs that stayed
        ``current`` (mtime fast path or hash match) are not in the
        return value.
        """
        refs = self.list()
        checks = []
        for ref in refs:
            if ref.origin != ORIGIN_FILE:
                continue
            p = self._resolve(ref.location)
            if force:
                # Skip the mtime fast path; always hash.
                try:
                    st = p.stat()
                except OSError:
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": None,
                        "mtime_ns": None,
                        "size_bytes": None,
                    })
                    continue
                try:
                    data = p.read_bytes()
                    current_fp = _sha256(data)
                    current_mtime_ns = getattr(st, "st_mtime_ns", None) or int(
                        st.st_mtime * 1_000_000_000
                    )
                    current_size = st.st_size
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": current_fp,
                        "mtime_ns": current_mtime_ns,
                        "size_bytes": current_size,
                    })
                except OSError:
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": None,
                        "mtime_ns": None,
                        "size_bytes": None,
                    })
            else:
                try:
                    st = p.stat()
                    current_mtime_ns = getattr(st, "st_mtime_ns", None) or int(
                        st.st_mtime * 1_000_000_000
                    )
                    current_size = st.st_size
                    if (
                        ref.mtime_ns is not None
                        and ref.size_bytes is not None
                        and ref.mtime_ns == current_mtime_ns
                        and ref.size_bytes == current_size
                    ):
                        # Fast path: mtime+size unchanged
                        checks.append({
                            "source_id": ref.id,
                            "fingerprint": ref.fingerprint,
                            "mtime_ns": current_mtime_ns,
                            "size_bytes": current_size,
                        })
                        continue
                    # Mtime/size differ: re-hash
                    try:
                        data = p.read_bytes()
                        current_fp = _sha256(data)
                        checks.append({
                            "source_id": ref.id,
                            "fingerprint": current_fp,
                            "mtime_ns": current_mtime_ns,
                            "size_bytes": current_size,
                        })
                    except OSError:
                        checks.append({
                            "source_id": ref.id,
                            "fingerprint": None,
                            "mtime_ns": None,
                            "size_bytes": None,
                        })
                except OSError:
                    checks.append({
                        "source_id": ref.id,
                        "fingerprint": None,
                        "mtime_ns": None,
                        "size_bytes": None,
                    })

        if not checks:
            return []

        result = self._send_command({
            "cmd": "source.refresh",
            "db": self._require_db(),
            "checks": checks,
            "ts": time.time(),
            "workspace": str(self.workspace) if self.workspace else "",
        })

        drifted: list[SourceRef] = []
        if isinstance(result, list):
            for r in result:
                drifted.append(SourceRef(
                    id=r["id"],
                    origin=r["origin"],
                    location=r["location"],
                    fingerprint=r["fingerprint"],
                    captured_at=r["captured_at"],
                    checked_at=r.get("checked_at"),
                    status=r["status"],
                    mtime_ns=r.get("mtime_ns"),
                    size_bytes=r.get("size_bytes"),
                    cited_ranges=r.get("cited_ranges", []),
                    permissions=r.get("permissions", {}),
                ))
        return drifted

    # -- citation validity (P3 §7.3 Source 完整能力) ----------------------------
    # Status (current / changed / missing) tells you whether the bytes
    # drifted. Citation validity is the stronger question: "if a UI
    # scrolled to the cited lines / page / cells right now, would it
    # land on the same content the run saw?" A changed file can still
    # resolve a lines citation (the lines happen to still mean the
    # same thing), but a missing file cannot; a lines citation past
    # EOF is unambiguously invalid even when the file is current.
    CITATION_VALID = "valid"
    CITATION_CONTENT_CHANGED = "content_changed"  # sha256 differs; UI can still navigate, but the cited bytes aren't what the run saw
    CITATION_OUT_OF_BOUNDS = "out_of_bounds"  # range past EOF in a current file (stale lines)
    CITATION_FILE_MISSING = "file_missing"  # status: missing → cannot navigate at all
    CITATION_SOURCE_GONE = "source_gone"  # ref removed from the store

    def validate_citation(
        self,
        ref_id: str,
        run_id: str,
        range_obj: dict[str, Any],
    ) -> dict[str, Any]:
        """One citation: is it still navigable to the same content?

        The result is a small dict (not a dataclass) so callers can
        thread it straight into a SourceCitationHit / DTO without an
        extra model. ``status`` mirrors the SourceRef status (``current``
        / ``changed`` / ``missing``) so the caller can refresh its
        freshness flag too; ``valid`` is the actionable boolean
        ("should the UI offer to scroll here?").

        ``range_obj`` is one entry from ``SourceRef.cited_ranges``'s
        ``ranges`` list (a canonical CitationRange dict with a ``kind``
        discriminator and the relevant locator fields).

        ``valid=True`` means: the file is current, the range resolves
        to actual content, and the cited bytes are what the run saw.

        ``valid=False`` reasons:

        - ``content_changed`` — file is ``changed`` (sha256 differs).
          The lines/page may still happen to point at something, but
          the run's evidence no longer corresponds to the live file.
        - ``out_of_bounds`` — file is current, but the range is past
          EOF (truncated file, stale ``start_line``). The UI can mark
          the citation as "stale" but cannot scroll to it.
        - ``file_missing`` — file is unreadable. The UI cannot
          navigate at all; the run is operating on a snapshot that
          no longer exists on disk.
        - ``source_gone`` — the ref itself was removed from the
          store. Cross-checked before filesystem access so a
          bookkeeping cleanup doesn't masquerade as a missing file.

        The final verdict is always computed by Rust ``delta_core``.
        File-level errors (missing path, stat OSError, hash OSError)
        are handled by the Rust side as part of the verdict.
        """
        result = self._send_command({
            "cmd": "citation.validate",
            "db": self._require_db(),
            "source_id": ref_id,
            "range": range_obj,
            "workspace": str(self.workspace) if self.workspace else "",
        })
        if not isinstance(result, dict):
            raise DeltaCoreError("citation.validate returned non-object result")
        return result


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


__all__ = [
    "CitationRange",
    "FRESH_CURRENT",
    "FRESH_CHANGED",
    "FRESH_MISSING",
    "KIND_CELLS",
    "KIND_COLUMN",
    "KIND_CUSTOM",
    "KIND_LINES",
    "KIND_MESSAGE_ID",
    "KIND_PAGE",
    "KIND_ROW",
    "KIND_SHEET",
    "ORIGIN_FILE",
    "ORIGIN_URL",
    "ORIGIN_CONNECTOR",
    "ORIGIN_DB",
    "ORIGIN_MANUAL",
    "SourceRef",
    "SourceStore",
    "normalize_cited_ranges",
    "to_dto",
    "to_range_dict",
]