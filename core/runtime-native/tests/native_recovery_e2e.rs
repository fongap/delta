use std::fs;

use delta_runtime_native::control_plane;
use delta_runtime_native::{
    ArtifactInput, ArtifactReader, ArtifactRegistryWriter, AutomationStore, InboxStore,
    LedgerReader, LedgerWriter,
};
use serde_json::json;
use sha2::{Digest, Sha256};

#[test]
fn native_restart_recovers_session_run_artifact_inbox_automation_and_ledger() {
    let temp = tempfile::tempdir().unwrap();
    let state = temp.path().join("state");
    let workspace = temp.path().join("workspace");
    fs::create_dir_all(&workspace).unwrap();

    control_plane::ensure_session(
        &state,
        "session-restart",
        Some(workspace.to_string_lossy().as_ref()),
        "test-model",
    )
    .unwrap();
    for frame in [
        json!({
            "type": "turn_start", "version": 1, "sessionId": "session-restart",
            "sequence": 1, "payload": {"input": "persist me", "run_id": "run-restart"}
        }),
        json!({
            "type": "assistant_message", "version": 1, "sessionId": "session-restart",
            "sequence": 2, "payload": {"message": {"role": "assistant", "content": "persisted"}}
        }),
        json!({
            "type": "turn_end", "version": 1, "sessionId": "session-restart",
            "sequence": 3, "payload": {"status": "completed", "iterations": 1}
        }),
    ] {
        control_plane::record_runtime_event(&state, &frame).unwrap();
    }

    let report = workspace.join("report.md");
    fs::write(&report, "durable artifact").unwrap();
    let sha256 = format!("{:x}", Sha256::digest(b"durable artifact"));
    let ledger_path = state.join("run_events.db");
    {
        let ledger = LedgerWriter::open(&ledger_path).unwrap();
        ledger
            .transition(
                "run-restart",
                "run.started",
                "user",
                1.0,
                &json!({"session_id": "session-restart"}),
                workspace.to_string_lossy().as_ref(),
            )
            .unwrap();
        ArtifactRegistryWriter::new(&ledger)
            .register(
                &ArtifactInput {
                    path: "report.md".to_string(),
                    name: "report.md".to_string(),
                    kind: "markdown".to_string(),
                    size: 16,
                    modified_at: 2.0,
                    run_id: "run-restart".to_string(),
                    sha256: sha256.clone(),
                    incomplete: false,
                    registered_at: 2.0,
                },
                2.0,
                workspace.to_string_lossy().as_ref(),
            )
            .unwrap();
        ledger
            .transition(
                "run-restart",
                "run.completed",
                "system",
                3.0,
                &json!({"status": "completed"}),
                workspace.to_string_lossy().as_ref(),
            )
            .unwrap();
    }

    let inbox_path = state.join("inbox.json");
    let inbox_id = {
        let inbox = InboxStore::open(&inbox_path).unwrap();
        inbox
            .add_approval(
                "session-restart",
                "call-restart",
                "write_report",
                &json!({"path": "report.md"}),
                "write access",
            )
            .unwrap()
            .id
    };

    let automation_id = {
        let automations = AutomationStore::open(&state).unwrap();
        automations
            .create(&json!({
                "title": "Daily report",
                "instructions": "Summarize the workspace",
                "workspace": workspace,
            }))
            .unwrap()["task"]["id"]
            .as_str()
            .unwrap()
            .to_string()
    };

    // Everything below is opened from disk again, modelling a fresh app boot.
    let transcript = control_plane::get_session_messages(&state, "session-restart").unwrap();
    assert_eq!(transcript["messages"].as_array().unwrap().len(), 2);
    assert_eq!(transcript["messages"][0]["content"], "persist me");
    assert_eq!(transcript["messages"][1]["content"], "persisted");

    let ledger = LedgerReader::open(&ledger_path).unwrap();
    assert_eq!(ledger.run_status("run-restart").unwrap(), "ok");
    assert!(ledger.verify("run-restart").unwrap());

    let artifacts = ArtifactReader::open(&ledger_path)
        .unwrap()
        .list_for_run("run-restart")
        .unwrap();
    assert_eq!(artifacts.len(), 1);
    assert_eq!(artifacts[0].path, "report.md");
    assert_eq!(artifacts[0].sha256, sha256);
    assert!(ArtifactReader::open(&ledger_path)
        .unwrap()
        .verify_against_workspace("run-restart", &workspace)
        .unwrap()
        .is_empty());

    let inbox = InboxStore::open(&inbox_path).unwrap();
    assert_eq!(inbox.get(&inbox_id).unwrap().state, "pending");

    let automations = AutomationStore::open(&state).unwrap();
    assert!(automations
        .list()
        .unwrap()
        .iter()
        .any(|task| task["id"] == automation_id));
}
