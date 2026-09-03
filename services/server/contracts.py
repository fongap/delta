"""Versioned UI/runtime boundary models.

These models describe the stable fields Delta surfaces may depend on. They deliberately
allow additive fields so provider/runtime internals can evolve without breaking the UI.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorEnvelope(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retriable: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_reserved_error_field(cls, value: Any) -> Any:
        if isinstance(value, dict) and "error" in value:
            raise ValueError("error is not a field in the runtime error envelope")
        return value


class EventEnvelopeV1(ContractModel):
    type: str
    version: Literal[1] = 1
    sessionId: str | None
    sequence: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_reserved_data_field(cls, value: Any) -> Any:
        if isinstance(value, dict) and "data" in value:
            raise ValueError("data is not a field in the runtime event envelope")
        return value


class SessionDTO(ContractModel):
    session_id: str
    workspace: str
    agent: str
    model: str
    mode: str
    title: str | None = None
    updated_at: str | None = None
    messages: int = Field(default=0, ge=0)
    pinned: bool = False
    archived: bool = False
    reasoning_effort: str = "auto"
    attention: int = Field(default=0, ge=0)
    liveness: Literal["working", "sleeping", "idle"] = "idle"
    subscriptions: list[str] = Field(default_factory=list)
    origin: str | None = None
    origin_label: str | None = None


class MessageDTO(ContractModel):
    role: str
    content: Any = None
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None
    source: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None


class ApprovalDTO(ContractModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    category: str = ""
    standing_target: str = ""


class ArtifactDTO(ContractModel):
    path: str
    name: str
    kind: str
    size: int = Field(ge=0)
    modified_at: float
    abs_path: str | None = None


class SourceDTO(ContractModel):
    id: str
    origin: str
    name: str
    fingerprint_prefix: str
    freshness: Literal["current", "changed", "missing"] = "current"
    # Workspace-relative path / URI / connector coordinate. P1 dropped this to
    # shrink the payload; P2 restores it so the UI can render the actual
    # location + let users click through to the file. Optional for backward
    # compat with older consumers that may still omit it.
    location: str | None = None
    # Per-run citations ({run_id, ranges}) linking runs → this source. Same
    # optional-additive policy as ``location``.
    cited_ranges: list[dict[str, Any]] = Field(default_factory=list)


class ModelDTO(ContractModel):
    id: str
    provider: str
    label: str | None = None
    available: bool = True
    custom_provider: bool = False


def error_envelope(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retriable: bool = False,
) -> dict[str, Any]:
    return ErrorEnvelope(
        code=code,
        message=message,
        details=details or {},
        retriable=retriable,
    ).model_dump()


def runtime_event_v1(
    event_type: str,
    session_id: str | None,
    sequence: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return EventEnvelopeV1(
        type=event_type,
        sessionId=session_id,
        sequence=sequence,
        payload=payload,
    ).model_dump()
