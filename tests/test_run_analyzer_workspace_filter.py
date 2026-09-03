"""P3 §10.6 step 2 — ``timeline_for_run`` 中的 ledger 端工作空间过滤。

先前：``timeline_for_run`` 调用了 ``ledger.events(run_id)`` 并返回了
该 run_id 的**所有**事件，而不受 Analyzer 绑定的 ``workspace`` 限制。
如果调用方传递了来自不同工作空间的 run_id（例如会话重用了 ID，
或操作员误操作），其时间线将静默地跨边界泄露事件。

此 PR (ADR-007 §10.6 step 2) 将过滤操作推送到 SQL 层：
``RunEventLedger.events_in_workspace(run_id, workspace)`` 使用
``idx_run_events_workspace (workspace, run_id, seq)`` 复合索引来代替
全量读取后再过滤。这些测试锁定了该契约。

契约总结：

- ``workspace=`` kwarg 是可选的；``None`` 将返回完整的 run（遗留的
  迁移前数据，或显式的跨工作空间意图）。
- 跨工作空间不是错误 —— 它返回 ``[]``，与“run 未找到”的形状相同
  （对于其他工作空间的调用方，不会泄露该 run 是否存在）。
- 该过滤器是一个 SQL 子句，因此对于具有选择性的工作空间，规划器
  可以使用复合索引。
- 除非手动传递，否则 Analyzer 自身的 ``self.workspace`` **不会**自动
  应用 —— 以免掩盖调用方的错误。
"""

from __future__ import annotations

from core.analyzer import Analyzer, TimelineEntry, timeline_for_run
from core.ledger import RunEventLedger


def _ledger_with_two_workspace_runs(tmp_path):
    """Set up a ledger with two runs in two different workspaces."""
    ws_a = tmp_path / "wsA"
    ws_a.mkdir()
    ws_b = tmp_path / "wsB"
    ws_b.mkdir()
    ledger = RunEventLedger(tmp_path / "events.db")
    # run-1 lives in workspace A: 3 events.
    ledger.append("run-1", "run.started", actor="system", payload={"k": 1}, workspace=str(ws_a))
    ledger.append(
        "run-1",
        "tool.finished",
        actor="engine",
        payload={"tool": "read_file", "status": "ok"},
        workspace=str(ws_a),
    )
    ledger.append("run-1", "run.completed", actor="system", payload={}, workspace=str(ws_a))
    # run-2 lives in workspace B: 2 events.
    ledger.append("run-2", "run.started", actor="system", payload={}, workspace=str(ws_b))
    ledger.append("run-2", "run.completed", actor="system", payload={}, workspace=str(ws_b))
    return ws_a, ws_b, ledger


def test_workspace_filter_returns_only_matching_rows(tmp_path):
    ws_a, _ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        tl = analyzer.timeline_for_run("run-1", workspace=str(ws_a))
    finally:
        ledger.close()
    assert [e.type for e in tl] == ["run.started", "tool.finished", "run.completed"]


def test_workspace_filter_empty_for_cross_workspace_run(tmp_path):
    """Asking for run-1 under workspace B (where run-1 doesn't live)
    returns ``[]`` — same shape as "run not found", so a caller scoped
    to B never learns whether run-1 exists at all."""
    ws_a, ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws_b), ledger=ledger)
        tl = analyzer.timeline_for_run("run-1", workspace=str(ws_b))
    finally:
        ledger.close()
    assert tl == []


def test_workspace_none_returns_full_run(tmp_path):
    """``workspace=None`` opts out of the filter — legacy pre-migration
    rows (no workspace column) or a caller with explicit cross-
    workspace intent can still see the full timeline."""
    ws_a, _ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        tl = analyzer.timeline_for_run("run-1", workspace=None)
    finally:
        ledger.close()
    assert len(tl) == 3


def test_analyzer_workspace_not_implicitly_applied(tmp_path):
    """If the caller forgets to pass ``workspace=``, the Analyzer's own
    bound workspace is NOT silently applied — masking caller mistakes
    would be worse than asking them to be explicit."""
    ws_a, _ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        # No workspace= kwarg; defaults to None → returns ALL events
        # for run-1 even though analyzer is bound to ws_a.
        tl = analyzer.timeline_for_run("run-1")
    finally:
        ledger.close()
    assert len(tl) == 3


def test_unknown_run_id_returns_empty_regardless_of_workspace(tmp_path):
    ws_a, _ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        tl = analyzer.timeline_for_run("does-not-exist", workspace=str(ws_a))
    finally:
        ledger.close()
    assert tl == []


def test_empty_run_id_rejected(tmp_path):
    ws_a = tmp_path / "wsA"
    ws_a.mkdir()
    ledger = RunEventLedger(tmp_path / "events.db")
    try:
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        try:
            analyzer.timeline_for_run("", workspace=str(ws_a))
        except ValueError as exc:
            assert "run_id" in str(exc)
        else:
            raise AssertionError("empty run_id must be rejected")
    finally:
        ledger.close()


def test_module_wrapper_threads_workspace_through(tmp_path):
    ws_a, _ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        via_module = timeline_for_run(
            workspace=str(ws_a), run_id="run-1", ledger=ledger
        )
    finally:
        ledger.close()
    assert len(via_module) == 3
    assert all(isinstance(e, TimelineEntry) for e in via_module)


def test_module_wrapper_empty_for_cross_workspace(tmp_path):
    ws_a, ws_b, ledger = _ledger_with_two_workspace_runs(tmp_path)
    try:
        # Ask for run-1 (which lives in ws_a) but scope to ws_b.
        via_module = timeline_for_run(
            workspace=str(ws_b), run_id="run-1", ledger=ledger
        )
    finally:
        ledger.close()
    assert via_module == []


def test_legacy_null_workspace_column_matches_empty_string_filter(tmp_path):
    """Pre-migration rows (workspace column is NULL) match an empty-
    string filter so callers asking 'show me the pre-migration events'
    still get them. The SQL translation is documented in
    :meth:`RunEventLedger.events_in_workspace`."""
    ws_a = tmp_path / "wsA"
    ws_a.mkdir()
    ledger = RunEventLedger(tmp_path / "events.db")
    try:
        # Append two events with workspace=None — the ledger stores
        # these as NULL in the column.
        ledger.append("r1", "run.started", workspace=None)
        ledger.append("r1", "run.completed", workspace=None)
        # An Analyzer scoped to ws_a can still pass workspace="" to ask
        # "show me the legacy NULL rows" — it's an explicit escape hatch
        # into pre-migration data, not a way to leak across workspaces.
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        tl = analyzer.timeline_for_run("r1", workspace="")
    finally:
        ledger.close()
    assert len(tl) == 2


def test_mixed_legacy_and_new_rows_filter_correctly(tmp_path):
    """A run that has some events written before the workspace column
    existed (NULL) and some after (with a real workspace) should split
    cleanly: the empty-string filter only matches the NULL rows, and a
    real workspace filter only matches the new rows."""
    ws_a = tmp_path / "wsA"
    ws_a.mkdir()
    ledger = RunEventLedger(tmp_path / "events.db")
    try:
        # Two pre-migration events (workspace=None).
        ledger.append("r1", "run.started", workspace=None)
        ledger.append("r1", "tool.started", workspace=None)
        # Two post-migration events under ws_a.
        ledger.append("r1", "tool.finished", workspace=str(ws_a))
        ledger.append("r1", "run.completed", workspace=str(ws_a))
        analyzer = Analyzer(workspace=str(ws_a), ledger=ledger)
        # Empty-string filter sees only the NULL rows.
        legacy_tl = analyzer.timeline_for_run("r1", workspace="")
        # Real-workspace filter sees only the new rows.
        new_tl = analyzer.timeline_for_run("r1", workspace=str(ws_a))
        # None filter sees everything.
        all_tl = analyzer.timeline_for_run("r1", workspace=None)
    finally:
        ledger.close()
    assert [e.type for e in legacy_tl] == ["run.started", "tool.started"]
    assert [e.type for e in new_tl] == ["tool.finished", "run.completed"]
    assert len(all_tl) == 4
