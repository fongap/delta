"""Schema gates for the stable Delta UI/runtime boundary."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.server.contracts import (
    ApprovalDTO,
    ArtifactDTO,
    ErrorEnvelope,
    EventEnvelopeV1,
    MessageDTO,
    ModelDTO,
    SessionDTO,
)
from coworker.sessions import SessionRecord


class NoopProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("contract response tests must not call the provider")

    def capabilities(self, model):
        return ModelCapabilities()


def test_current_session_and_message_responses_match_core_schemas(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=NoopProvider())
    manager.session_store.save(
        SessionRecord(
            session_id="contract-s1",
            workspace=str(tmp_path),
            model="custom:model",
            mode="interactive",
            agent="cowork",
            messages=[{"role": "user", "content": "hello"}],
        )
    )
    client = TestClient(create_app(manager))

    sessions = client.get("/v1/sessions").json()["sessions"]
    SessionDTO.model_validate(sessions[0])
    messages = client.get("/v1/sessions/contract-s1/messages").json()["messages"]
    MessageDTO.model_validate(messages[0])


def test_core_dto_schemas_allow_additive_fields_and_apply_defaults():
    session = SessionDTO.model_validate(
        {
            "session_id": "s1",
            "workspace": "C:/work",
            "agent": "cowork",
            "model": "alias:model",
            "mode": "interactive",
            "future_field": "ignored by existing consumers",
        }
    )
    assert session.messages == 0
    assert session.reasoning_effort == "auto"
    assert session.liveness == "idle"

    message = MessageDTO.model_validate(
        {"role": "assistant", "content": "hello", "future_field": True}
    )
    assert message.tool_calls is None

    approval = ApprovalDTO.model_validate(
        {"name": "write_file", "future_field": {"nested": True}}
    )
    assert approval.arguments == {}
    assert approval.reason == ""

    artifact = ArtifactDTO.model_validate(
        {
            "path": "result.md",
            "name": "result.md",
            "kind": "markdown",
            "size": 12,
            "modified_at": 1.0,
            "future_field": "safe",
        }
    )
    assert artifact.abs_path is None

    model = ModelDTO.model_validate(
        {"id": "local:model", "provider": "local", "future_field": 1}
    )
    assert model.available is True
    assert model.custom_provider is False


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            SessionDTO,
            {
                "workspace": "C:/work",
                "agent": "cowork",
                "model": "m",
                "mode": "interactive",
            },
        ),
        (MessageDTO, {"content": "missing role"}),
        (ApprovalDTO, {"arguments": {}}),
        (ArtifactDTO, {"name": "a", "kind": "text", "size": 1, "modified_at": 1}),
        (ModelDTO, {"id": "m"}),
    ],
)
def test_core_dto_schemas_reject_missing_required_fields(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_error_envelope_requires_stable_fields():
    valid = ErrorEnvelope.model_validate(
        {
            "code": "runtime.failed",
            "message": "failed",
            "details": {},
            "retriable": True,
            "extra": "forward compatible",
        }
    )
    assert valid.retriable is True

    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate({"message": "missing code"})


def test_contracts_reject_forbidden_wire_fields_but_allow_unknown_fields():
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate(
            {
                "code": "runtime.failed",
                "message": "failed",
                "details": {},
                "retriable": False,
                "error": "forbidden field",
            }
        )

    current = {
        "type": "ready",
        "version": 1,
        "sessionId": "s1",
        "sequence": 1,
        "payload": {},
        "future_field": True,
    }
    EventEnvelopeV1.model_validate(current)
    app_wide = EventEnvelopeV1.model_validate({**current, "sessionId": None})
    assert app_wide.sessionId is None
    with pytest.raises(ValidationError):
        EventEnvelopeV1.model_validate({**current, "data": {}})
