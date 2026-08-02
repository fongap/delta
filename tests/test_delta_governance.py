from coworker.delta_governance import GovernanceStore
from coworker.server.manager import SessionManager
from types import SimpleNamespace


def test_governance_store_replays_task_evidence(tmp_path):
    store = GovernanceStore(tmp_path / "delta.sqlite")
    store.record_artifact("task-1", "report.md", "document", citations=[{"source": "brief.md"}])
    store.record_approval("task-1", "write_file", "approved", reason="reviewed")
    store.checkpoint("task-1", "draft complete", "validate citations", risks=["missing source"])

    replay = store.replay("task-1")
    assert replay["artifacts"][0]["path"] == "report.md"
    assert replay["approvals"][0]["decision"] == "approved"
    assert replay["checkpoints"][0]["next_step"] == "validate citations"


def test_resolved_approval_is_added_to_the_task_replay(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data")
    request = SimpleNamespace(tool_name="write_file", reason="write report")

    manager.approval_outcome("deny", request, "task-2")

    replay = manager.governance_store.replay("task-2")
    assert replay["approvals"][0]["action"] == "write_file"
    assert replay["approvals"][0]["decision"] == "deny"


def test_successful_file_write_is_added_as_an_artifact(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data")

    manager.record_execution_event(
        {"session_id": "task-3", "tool": "write_file", "stage": "finished", "status": "ok", "arguments": {"path": "report.md"}}
    )

    assert manager.governance_store.replay("task-3")["artifacts"][0]["path"] == "report.md"
