from coworker.delta_governance import GovernanceStore


def test_governance_store_replays_task_evidence(tmp_path):
    store = GovernanceStore(tmp_path / "delta.sqlite")
    store.record_artifact("task-1", "report.md", "document", citations=[{"source": "brief.md"}])
    store.record_approval("task-1", "write_file", "approved", reason="reviewed")
    store.checkpoint("task-1", "draft complete", "validate citations", risks=["missing source"])

    replay = store.replay("task-1")
    assert replay["artifacts"][0]["path"] == "report.md"
    assert replay["approvals"][0]["decision"] == "approved"
    assert replay["checkpoints"][0]["next_step"] == "validate citations"
