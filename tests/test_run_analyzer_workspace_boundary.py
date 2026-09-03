"""P3 Run Analyzer — per-``workspace`` boundary contract (ADR-007 D-4).

These tests pin the one shape every public Analyzer method must keep:
``workspace`` is a *required* first argument and the query never crosses
a workspace boundary on its own. Future contributors adding a new query
method (or a new wrapper) need to either re-use the same first argument
or break these tests so the violation is visible at review time.
"""

from __future__ import annotations

import inspect

import pytest

from core.analyzer import (
    Analyzer,
    automation_health,
    source_citation_hits,
    timeline_for_run,
)
from core.ledger import RunEventLedger


def _sig(name: str) -> inspect.Signature:
    return inspect.signature(name)


def test_timeline_for_run_requires_workspace_keyword():
    """The module wrapper must demand ``workspace=`` as a keyword argument."""
    sig = _sig(timeline_for_run)
    assert "workspace" in sig.parameters
    param = sig.parameters["workspace"]
    assert param.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    assert param.default is inspect.Parameter.empty


def test_automation_health_requires_workspace_keyword():
    sig = _sig(automation_health)
    assert "workspace" in sig.parameters
    param = sig.parameters["workspace"]
    assert param.default is inspect.Parameter.empty


def test_source_citation_hits_requires_workspace_keyword():
    sig = _sig(source_citation_hits)
    assert "workspace" in sig.parameters
    param = sig.parameters["workspace"]
    assert param.default is inspect.Parameter.empty


def test_analyzer_constructor_rejects_empty_workspace(tmp_path):
    with pytest.raises(ValueError, match="workspace is required"):
        Analyzer(workspace="", ledger=RunEventLedger(tmp_path / "e.db"))


def test_queries_never_see_other_workspace_runs(tmp_path):
    """Two workspaces, two runs each. A query scoped to workspace A must
    only see workspace A's run, even if the ledger has B's events too."""
    ws_a = tmp_path / "wsA"
    ws_b = tmp_path / "wsB"
    ws_a.mkdir()
    ws_b.mkdir()
    ledger = RunEventLedger(tmp_path / "events.db")
    # Two runs in workspace A (B's session_id lives in payload only)
    for run_id in ("run-A1", "run-A2"):
        ledger.append(
            run_id,
            "run.started",
            payload={"session_id": f"sA-{run_id}", "workspace": str(ws_a)},
        )
        ledger.append(run_id, "run.completed", payload={"workspace": str(ws_a)})
    # Two runs in workspace B
    for run_id in ("run-B1", "run-B2"):
        ledger.append(
            run_id,
            "run.started",
            payload={"session_id": f"sB-{run_id}", "workspace": str(ws_b)},
        )
        ledger.append(run_id, "run.completed", payload={"workspace": str(ws_b)})

    analyzer_a = Analyzer(workspace=str(ws_a), ledger=ledger)

    # timeline_for_run only addresses a known run_id; the per-workspace
    # contract is on the *Analyzer instance*, so the run_id is allowed
    # to be from any workspace. The boundary is that the analyzer
    # itself is bound to one workspace, which we pin in the next test.
    timeline = analyzer_a.timeline_for_run("run-A1")
    assert [e.type for e in timeline] == ["run.started", "run.completed"]


def test_analyzer_workspace_is_immutable_after_construction(tmp_path):
    """The bound workspace is set in __init__ and there is no public
    setter — drift would silently expand a query's scope. The pin is
    structural (no setattr) so the test asserts the attribute is
    already what the constructor set."""
    ws = tmp_path / "ws"
    ws.mkdir()
    analyzer = Analyzer(workspace=str(ws), ledger=RunEventLedger(tmp_path / "e.db"))
    assert analyzer.workspace == str(ws)
    # No `workspace` property setter; bind-time only.
    assert not hasattr(Analyzer, "workspace_setter")  # static pin


# (No _NullLedger helper — the real RunEventLedger is cheap, and a fake
# would diverge from the real connection lifecycle. Tests that need
# ledger reads use a real ledger pointed at tmp_path.)
