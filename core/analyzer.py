"""P3 长期「智能」 — 只读 Run Analyzer (ADR-007).

Delta 当前"项目"的事实锚 = ``workspace``（``SessionRecord.workspace`` /
``audit_events.workspace`` / ``memories.workspace`` / ``<workspace>/.delta/sources.json``
都是一等字段；全仓零 ``project_id`` 概念）。本模块提供 per-workspace
只读投影，不写任何候选 / 经验 / 失败记忆 / 偏好 / Skill 状态，不引入第二个
事实库（§10.6 "ledger 是单一事实来源"）。

边界（ADR-007 D-4）：

- 所有 query 第一参数 ``workspace: str``，**不允许**"全局 fetch + 客户端过滤"
- 同一 workspace 内支持跨 session / 跨 TaskRun 聚合
- **不**做跨 workspace 聚合；如需 project 维度叠加，由独立 ADR 引入
  ``project_id`` 概念

暴露：

- :func:`timeline_for_run` — Run 端到端还原（按 seq 排序的 TimelineEntry）
- :func:`automation_health` — 跨 N 个 run 的 TaskRun.status / validation 聚合
- :func:`source_citation_hits` — SourceRef.cited_ranges + ledger tool.finished
  关联
- :class:`Analyzer` — 持有 RunEventLedger / SourceStore / TaskStore 引用，
  方便注入单测 fake（三个公共函数是该类的薄包装）
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.automation.models import ScheduledTask
from core.automation.store import TaskStore
from core.ledger import KNOWN_EVENT_TYPES, RunEventLedger
from core.sources import SourceStore


@dataclass
class TimelineEntry:
    """One line of the run's end-to-end story.

    Built directly from a ``run_events`` row; no extra tables, no second
    cache. ``payload`` is the sanitized JSON dict stored by the ledger —
    consumers can read e.g. ``payload["tool"]`` / ``payload["status"]`` /
    ``payload["reason"]`` without re-fetching anything.
    """

    seq: int
    type: str
    actor: str
    ts: float
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "actor": self.actor,
            "ts": self.ts,
            "payload": dict(self.payload),
        }


@dataclass
class AutomationHealth:
    """Aggregate view of one ``ScheduledTask``'s recent runs.

    Per ADR-007 D-1, the rollup is read-only: it consumes the existing
    ``TaskRun`` rows + their ledger events; it does not modify
    ``ScheduledTask.last_status`` or any other state field. The
    ``failure_reasons`` counter is built from the run's
    ``run.failed`` / ``validation.failed`` /
    ``tool.finished{status: "error"}`` payloads (the most specific
    signals we have). The top-level ``TaskRun.error`` string is
    surfaced separately as ``run_error_counts`` so it isn't double-
    counted against the same root cause carried by the ledger events.
    """

    task_id: str
    workspace: str
    window: int
    run_count: int
    status_counts: dict[str, int]
    failure_rate: float
    avg_duration_seconds: float | None
    failure_reasons: dict[str, int]
    run_error_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "window": self.window,
            "run_count": self.run_count,
            "status_counts": dict(self.status_counts),
            "failure_rate": self.failure_rate,
            "avg_duration_seconds": self.avg_duration_seconds,
            "failure_reasons": dict(self.failure_reasons),
            "run_error_counts": dict(self.run_error_counts),
        }


@dataclass
class SourceCitationHit:
    """One read/citation signal linking a source to a run.

    The shape keeps the original ``SourceRef.cited_ranges`` entry intact
    (under ``citation``) and joins it with the ledger's
    ``tool.finished`` payload when available, so the caller can answer
    "what did run X actually read, and on which attempt".
    """

    source_id: str
    location: str
    run_id: str
    captured_at: str
    citation: dict[str, Any]
    ledger_payload: dict[str, Any] | None
    # Per-citation validity (P3 §7.3 Source 完整能力). The UI can use
    # ``valid`` to decide whether to offer "scroll to the lines" vs.
    # "the file has changed since this run"; ``reason`` is the precise
    # cause so it can render the right notice.
    validity: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "location": self.location,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "citation": dict(self.citation),
            "ledger_payload": dict(self.ledger_payload) if self.ledger_payload else None,
            "validity": dict(self.validity) if self.validity else None,
        }


class WorkspaceMismatchError(ValueError):
    """Raised when a query's ``workspace`` argument doesn't match the
    fact the underlying record was captured under.

    Per ADR-007 D-4 the Analyzer never silently mixes data across
    workspaces. A record whose ``workspace`` is unknown (e.g. legacy
    rows without a workspace column) is reported as ``None`` rather
    than guessed; a record whose ``workspace`` we *can* read but
    disagrees with the query argument is rejected loudly.
    """


class Analyzer:
    """The per-workspace read-only projection.

    Holds a :class:`RunEventLedger`, a per-workspace
    :class:`SourceStore`, and a :class:`TaskStore` (the same stores the
    runtime uses). Construction is cheap and side-effect-free: it does
    not append to any table, mutate any source ref, or call any model.

    Tests construct an :class:`Analyzer` directly with the stores they
    drive; production code is expected to receive one from the
    SessionManager (or future call sites) so the wiring stays in one
    place.
    """

    def __init__(
        self,
        *,
        workspace: str,
        ledger: RunEventLedger,
        task_store: TaskStore | None = None,
        source_store: SourceStore | None = None,
    ) -> None:
        """The per-workspace read-only projection.

        ``ledger`` is mandatory — every query reads from the run event
        ledger. ``task_store`` is required for ``automation_health``;
        ``source_store`` is required for ``source_citation_hits``. The
        constructor accepts ``None`` for the two query-specific stores
        so a caller that only needs the timeline can omit them without
        building a temporary file.
        """
        if not workspace:
            raise ValueError("workspace is required (ADR-007 D-4)")
        self.workspace = str(workspace)
        self.ledger = ledger
        self.task_store = task_store
        self.source_store = source_store

    # -- run timeline ----------------------------------------------------------

    def timeline_for_run(
        self, run_id: str, *, workspace: str | None = None
    ) -> list[TimelineEntry]:
        """Return the run's end-to-end story, in event order.

        When ``workspace`` is provided, the read is pushed to SQL
        (``WHERE run_id = ? AND workspace = ?``) so the planner can use
        the ``idx_run_events_workspace (workspace, run_id, seq)``
        compound index (ADR-007 §10.6 step 1) instead of doing the
        filter in Python after a full ledger read. Cross-workspace
        reads are **not** an error: a run that exists under a different
        workspace simply returns ``[]``, the same shape as "run not
        found" — this avoids leaking whether the run exists at all to a
        caller without the right scope, and keeps iteration over many
        ``run_id``s cheap.

        When ``workspace`` is ``None`` the full run is returned (legacy
        pre-migration data, or a caller with explicit cross-workspace
        intent). We deliberately do **not** silently fall back to
        ``self.workspace`` — that would mask caller mistakes and make
        the filter opt-out by accident.

        We do not filter by event type here: the closed vocabulary is
        ``KNOWN_EVENT_TYPES`` (ADR-005 WS1) and a missing type surfaces
        in the UI exactly as the ledger stored it, so future event
        types do not silently disappear from the timeline.

        The returned list is ordered by ``seq`` ascending and is a copy
        of the ledger's view; mutating it does not touch persistence.
        """
        if not run_id:
            raise ValueError("run_id is required")
        if workspace is not None:
            events = self.ledger.events_in_workspace(run_id, workspace)
        else:
            events = self.ledger.events(run_id)
        return [
            TimelineEntry(
                seq=row["seq"],
                type=row["type"],
                actor=row["actor"],
                ts=row["ts"],
                payload=row.get("payload") or {},
            )
            for row in events
        ]

    # -- automation health ------------------------------------------------------

    def automation_health(
        self, task_id: str, *, window: int = 20
    ) -> AutomationHealth:
        """Per-``ScheduledTask`` rollup, scoped to ``self.workspace``.

        ``window`` is the maximum number of most-recent runs to consider.
        A task that is not bound to ``self.workspace`` (cross-workspace
        or stale) raises :class:`WorkspaceMismatchError` — the analyzer
        never silently aggregates across boundaries.
        """
        if self.task_store is None:
            raise ValueError(
                "automation_health requires a task_store; "
                "construct Analyzer(..., task_store=TaskStore(...))"
            )
        if window <= 0:
            raise ValueError("window must be a positive integer")
        if not task_id:
            raise ValueError("task_id is required")
        task = self.task_store.get(task_id)
        if task is None:
            raise WorkspaceMismatchError(f"unknown task_id: {task_id!r}")
        self._check_task_workspace(task)

        runs = self.task_store.runs(task_id, limit=window)
        status_counts: Counter[str] = Counter()
        durations: list[float] = []
        failure_reasons: Counter[str] = Counter()
        run_error_counts: Counter[str] = Counter()
        for run in runs:
            status_counts[run.status] += 1
            if run.finished_at is not None and run.started_at:
                durations.append(max(0.0, run.finished_at - run.started_at))
            if run.status in ("error", "validation_failed"):
                # TaskRun.error is a top-level label; ledger events are
                # detailed sub-causes. Keep them in separate counters so
                # the same root cause isn't double-counted.
                if run.error:
                    run_error_counts[run.error] += 1
                self._accumulate_ledger_failures(run.run_id, failure_reasons)

        run_count = len(runs)
        failure_total = sum(
            status_counts.get(k, 0) for k in ("error", "validation_failed")
        )
        failure_rate = (failure_total / run_count) if run_count else 0.0
        avg_duration = (sum(durations) / len(durations)) if durations else None

        return AutomationHealth(
            task_id=task_id,
            workspace=self.workspace,
            window=window,
            run_count=run_count,
            status_counts=dict(status_counts),
            failure_rate=failure_rate,
            avg_duration_seconds=avg_duration,
            failure_reasons=dict(failure_reasons),
            run_error_counts=dict(run_error_counts),
        )

    # -- source / citation -----------------------------------------------------

    def source_citation_hits(
        self, source_id: str
    ) -> list[SourceCitationHit]:
        """All citation signals on one source, joined with the ledger.

        Returns one :class:`SourceCitationHit` per ``SourceRef.cited_ranges``
        entry; the ``ledger_payload`` is filled when the ledger has a
        ``tool.finished`` event for the same ``run_id`` and ``tool``
        (``read_file`` / ``read_document``), so callers can correlate
        "this citation belongs to this exact read".
        """
        if self.source_store is None:
            raise ValueError(
                "this Analyzer has no source_store bound; "
                "construct it with source_store=SourceStore(...)"
            )
        if not source_id:
            raise ValueError("source_id is required")
        ref = self.source_store.get(source_id)
        if ref is None:
            return []
        # Per ADR-007 D-4: the source ref is implicitly bound to whatever
        # workspace its store was constructed for; we treat that as the
        # boundary, not as something to re-derive from path strings.
        hits: list[SourceCitationHit] = []
        for citation in ref.cited_ranges:
            run_id = citation.get("run_id")
            if not run_id:
                continue
            # P3 §7.3 Source 完整能力: every hit carries a per-citation
            # validity result so the UI can warn when a cited range no
            # longer resolves to the same content the run saw. We check
            # every range in the entry (a single run may cite several
            # ranges of the same file) and roll them up to the worst
            # status so the UI gets one signal per (source, run).
            ranges = citation.get("ranges") or []
            validity_results = [
                self.source_store.validate_citation(ref.id, run_id, r) for r in ranges
            ]
            if validity_results:
                worst = _worst_validity(validity_results)
            else:
                worst = None
            hits.append(
                SourceCitationHit(
                    source_id=ref.id,
                    location=ref.location,
                    run_id=run_id,
                    captured_at=ref.captured_at,
                    citation=dict(citation),
                    ledger_payload=self._ledger_payload_for_read(run_id),
                    validity=worst,
                )
            )
        hits.sort(key=lambda h: h.captured_at, reverse=True)
        return hits

    # -- helpers ---------------------------------------------------------------

    def _check_task_workspace(self, task: ScheduledTask) -> None:
        """Reject tasks that are not bound to ``self.workspace``.

        ``ScheduledTask.workspace`` is the only project anchor for a task
        (ADR-007 D-0 audit). If the stored workspace doesn't match the
        analyzer's bound workspace we refuse the query — silent mixing
        would produce meaningless cross-project rollups.
        """
        if not task.workspace:
            raise WorkspaceMismatchError(
                f"task {task.id!r} has no workspace; cannot scope to {self.workspace!r}"
            )
        if _norm_workspace(task.workspace) != _norm_workspace(self.workspace):
            raise WorkspaceMismatchError(
                f"task {task.id!r} is bound to {task.workspace!r}, "
                f"analyzer is scoped to {self.workspace!r}"
            )

    def _accumulate_ledger_failures(
        self, run_id: str, into: Counter[str]
    ) -> None:
        """Fold the run's failure-shaped events into ``into``.

        We only walk events whose ``type`` carries a failure payload —
        ``run.failed``, ``tool.finished{status: "error"}``,
        ``validation.failed`` — and bucket by the most specific reason
        we can read from the payload. No synthesized labels; the
        caller can map them to user-facing copy.
        """
        for row in self.ledger.events(run_id):
            t = row["type"]
            payload = row.get("payload") or {}
            reason: str | None = None
            if t == "run.failed":
                reason = payload.get("reason") or payload.get("error")
            elif t == "validation.failed":
                reason = (
                    payload.get("reason")
                    or payload.get("check")
                    or payload.get("error")
                )
            elif t == "tool.finished" and payload.get("status") == "error":
                reason = (
                    payload.get("error")
                    or payload.get("tool")
                    or "tool error"
                )
            if reason:
                into[str(reason)] += 1

    def _ledger_payload_for_read(self, run_id: str) -> dict[str, Any] | None:
        """Return the most recent ``tool.finished`` payload for a read tool.

        The join is best-effort: if the run has no such event (e.g. the
        citation came from a different reader, or the run never reached
        ``tool.finished``) we return ``None`` rather than guess. This
        keeps the projection honest about what the ledger actually saw.
        """
        last: dict[str, Any] | None = None
        for row in self.ledger.events(run_id):
            if row["type"] != "tool.finished":
                continue
            payload = row.get("payload") or {}
            tool = payload.get("tool") or payload.get("name")
            if tool in ("read_file", "read_document"):
                last = dict(payload)
        return last


def _norm_workspace(workspace: str | Path) -> str:
    """Resolve to a canonical absolute-posix form for cross-OS comparison.

    Two workspaces that point at the same folder on disk should compare
    equal; this avoids spurious ``WorkspaceMismatchError`` on Windows
    path spellings (``C:\\foo`` vs ``C:/foo`` vs trailing slashes).
    """
    return Path(workspace).expanduser().resolve().as_posix()


# Worst-validity rank — a single (source, run) hit may cite several
# ranges, and the UI wants one signal per hit. The rollup picks the
# most actionable reason (the one that prevents navigation). "valid"
# is the floor; missing > out_of_bounds > content_changed > valid.
_VALIDITY_RANK = {
    "valid": 0,
    "content_changed": 1,
    "out_of_bounds": 2,
    "file_missing": 3,
    "source_gone": 4,
}


def _worst_validity(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-range validity results into one per-hit signal.

    Picks the highest-ranked reason; ties keep the first occurrence
    so the rollup is deterministic across re-runs. The chosen
    entry's other keys (``current_sha256`` / ``current_line_count``)
    propagate so the UI can show them without re-querying.
    """
    worst = max(results, key=lambda r: _VALIDITY_RANK.get(r.get("reason", "valid"), 0))
    return dict(worst)


# -- module-level convenience wrappers --------------------------------------


def timeline_for_run(
    *,
    workspace: str,
    run_id: str,
    ledger: RunEventLedger,
) -> list[TimelineEntry]:
    """Module-level wrapper: one :meth:`Analyzer.timeline_for_run` call
    scoped to ``workspace``.

    ``workspace`` is passed to the underlying SQL filter so the call
    uses the ``idx_run_events_workspace`` compound index (ADR-007
    §10.6 step 2). Callers that need more than one query should
    construct an :class:`Analyzer` once and reuse it; the constructor
    only validates ``workspace`` and is side-effect-free.
    """
    return Analyzer(workspace=workspace, ledger=ledger).timeline_for_run(
        run_id, workspace=workspace
    )


def automation_health(
    *,
    workspace: str,
    task_id: str,
    task_store: TaskStore,
    ledger: RunEventLedger | None = None,
    window: int = 20,
) -> AutomationHealth:
    """Module-level wrapper for one :meth:`Analyzer.automation_health` call.

    ``ledger`` is optional; if omitted, the wrapper opens an isolated
    :class:`RunEventLedger` against a temp file so callers that only
    have a :class:`TaskStore` can still use the helper.
    """
    if ledger is None:
        ledger = _isolated_ledger()
    return Analyzer(
        workspace=workspace,
        ledger=ledger,
        task_store=task_store,
    ).automation_health(task_id, window=window)


def source_citation_hits(
    *,
    workspace: str,
    source_id: str,
    source_store: SourceStore,
    ledger: RunEventLedger,
) -> list[SourceCitationHit]:
    """Module-level wrapper for one :meth:`Analyzer.source_citation_hits` call."""
    return Analyzer(
        workspace=workspace,
        ledger=ledger,
        source_store=source_store,
    ).source_citation_hits(source_id)


def _isolated_ledger() -> RunEventLedger:
    """A :class:`RunEventLedger` pointed at a fresh tempfile.

    Used by the module-level wrappers that need a ledger object but
    don't have one in hand. The temp file is created in the system
    temp dir and unlinked immediately so the constructor's "open or
    create" lands on a clean path; the ledger is the caller's to
    close if they need to.
    """
    import tempfile

    fd, path = tempfile.mkstemp(prefix="analyzer-null-ledger-", suffix=".db")
    # mkstemp returns an open fd; we don't need it, the ledger will
    # open its own connection. Close and unlink so the constructor's
    # "open or create" lands on a clean path.
    import os

    os.close(fd)
    Path(path).unlink()
    return RunEventLedger(path)


__all__ = [
    "Analyzer",
    "AutomationHealth",
    "KNOWN_EVENT_TYPES",
    "SourceCitationHit",
    "TimelineEntry",
    "WorkspaceMismatchError",
    "automation_health",
    "source_citation_hits",
    "timeline_for_run",
]
