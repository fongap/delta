"""Validation — the deterministic gate between "ran" and "done" (ADR-005 WS3).

The blueprint requires:

  - "Validation 未通过 → Task 不得进入成功状态"
  - "Validation can judge whether the result meets the requirements"

Until now a TaskRun flipped to `status="ok"` whenever the engine returned
without raising — the actual result was never checked. This module introduces:

  - `ValidationCriteria` — the contract a task declares up-front
  - `ValidationCheck` — one named predicate + its verdict + evidence
  - `ValidationResult` — the aggregate verdict + check list + evidence
  - `run_validation` — evaluates criteria against the run's artifacts
  - `gate_status` — decides TaskRun.status from the result

The criteria set is deliberately small and deterministic. It is NOT an LLM
judge; it is a rule engine that looks at concrete, addressable facts:
artifact count, file existence, sha256 match, min/max size, required
substring in an artifact, header presence for CSV. Anything the model can
"say" must be grounded in these facts first.

After ADR-028 (Validation Hard-Cut):
  - Rust `delta_core` is the **sole Validation authority** (rules,
    verdicts, persistence).
  - Python performs extraction / candidate construction only.
  - No Python fallback, dual-write, shadow path, or migration delegate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.artifact import Artifact


@dataclass
class ValidationCriteria:
    """The deterministic completion contract for a run.

    `min_artifacts` / `max_artifacts` — gate on artifact count (default 1, 50).
    `required_paths` — every path must exist among the run's artifacts.
    `required_substrings` — `{path: [substring, ...]}` each substring must appear.
    `min_size` / `max_size` — per-file size gate `{path: bytes}`.
    `require_complete` — fail if any artifact is marked `incomplete=True`.
    `csv_required_headers` — `{path: [header, ...]}` for CSV artifacts.
    `require_citations` — fail if the run has fewer than `min_valid_citations`
        valid citations. P1-D Citation Completion Contract: an evidence-bearing
        task can demand a citation floor.
    `min_valid_citations` — the minimum number of citations whose validity is
        "valid" (per Source validity check). 0 disables the floor.
    """

    min_artifacts: int = 1
    max_artifacts: int = 50
    required_paths: list[str] = field(default_factory=list)
    required_substrings: dict[str, list[str]] = field(default_factory=dict)
    min_size: dict[str, int] = field(default_factory=dict)
    max_size: dict[str, int] = field(default_factory=dict)
    require_complete: bool = True
    csv_required_headers: dict[str, list[str]] = field(default_factory=dict)
    require_citations: bool = False
    min_valid_citations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_artifacts": self.min_artifacts,
            "max_artifacts": self.max_artifacts,
            "required_paths": list(self.required_paths),
            "required_substrings": {k: list(v) for k, v in self.required_substrings.items()},
            "min_size": dict(self.min_size),
            "max_size": dict(self.max_size),
            "require_complete": self.require_complete,
            "csv_required_headers": {k: list(v) for k, v in self.csv_required_headers.items()},
            "require_citations": self.require_citations,
            "min_valid_citations": self.min_valid_citations,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ValidationCriteria":
        return cls(
            min_artifacts=int(d.get("min_artifacts", 1)),
            max_artifacts=int(d.get("max_artifacts", 50)),
            required_paths=list(d.get("required_paths", [])),
            required_substrings={k: list(v) for k, v in d.get("required_substrings", {}).items()},
            min_size={k: int(v) for k, v in d.get("min_size", {}).items()},
            max_size={k: int(v) for k, v in d.get("max_size", {}).items()},
            require_complete=bool(d.get("require_complete", True)),
            csv_required_headers={k: list(v) for k, v in d.get("csv_required_headers", {}).items()},
            require_citations=bool(d.get("require_citations", False)),
            min_valid_citations=int(d.get("min_valid_citations", 0)),
        )


@dataclass
class ValidationCheck:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ValidationResult:
    ok: bool
    checks: list[ValidationCheck] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ValidationResult":
        return cls(
            ok=bool(d.get("ok", False)),
            checks=[ValidationCheck(**c) for c in d.get("checks", [])],
            evidence=dict(d.get("evidence", {})),
        )


# Default criteria: a run with no declared contract is "valid as long as the
# engine didn't crash and every artifact is readable". The blueprint's
# per-task acceptance ("at least one artifact", "this exact file", "contains
# this string") is the per-task author's choice — the floor here is the
# minimum the system will silently accept, not the bar for a "done" task.
DEFAULT_CRITERIA = ValidationCriteria(min_artifacts=0, max_artifacts=50)


def run_validation(
    artifacts: list["Artifact"],
    criteria: ValidationCriteria,
    *,
    workspace: str | None = None,
    valid_citation_count: int | None = None,
) -> ValidationResult:
    """Evaluate the criteria against the run's artifacts.

    `artifacts` may be `Artifact` instances or dicts (the in-memory shape used
    after `register_run_artifacts`). `workspace` is the absolute path used to
    resolve relative paths when reading file contents (substring + CSV checks).

    `valid_citation_count` enables the P1-D Citation Completion Contract: when
    the criteria declare `require_citations=True` and `min_valid_citations > 0`,
    the run fails if the count is below the floor. Pass None (the default) to
    skip the citation check entirely.

    This is a thin facade over `DeltaCoreClient` — all rule evaluation and
    persistence happens in Rust. Authority failures fail closed.
    """
    from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

    norm: list[dict[str, Any]] = []
    for a in artifacts:
        if isinstance(a, dict):
            norm.append(a)
        else:
            norm.append(a.to_dict())

    try:
        client = DeltaCoreClient()
        result = client.command(
            {
                "cmd": "validation.run",
                "artifacts": norm,
                "criteria": criteria.to_dict(),
                "workspace": workspace,
                "valid_citation_count": valid_citation_count,
            }
        )
        return ValidationResult.from_dict(result)
    except DeltaCoreError as exc:
        raise ValidationAuthorityError(f"Rust validation authority unavailable: {exc}") from exc


def gate_status(
    result: ValidationResult,
    *,
    engine_succeeded: bool,
    default_on_no_criteria: bool = True,
) -> str:
    """Map a validation result + engine outcome to a TaskRun.status string.

    The blueprint's two non-negotiable rules:

      - "Validation 未通过 → Task 不得进入成功状态"
      - The engine raising is the only path to `status="error"`

    A failed validation produces `"validation_failed"` (distinct from
    `"error"`) so the run is reportable and the user can see WHY without it
    looking like a crash. A successful validation with the engine succeeding
    produces `"ok"`. If no criteria were provided and the engine succeeded,
    `default_on_no_criteria` decides — defaults to `True` (the safe floor
    for an under-specified coding run).
    """
    if not engine_succeeded:
        return "error"
    if not result.ok:
        return "validation_failed"
    return "ok"


class ValidationAuthorityError(RuntimeError):
    """Raised when the Rust validation authority is unavailable.

    No fallback path exists — this is the hard-cut guarantee (ADR-028).
    """
    pass


def register_validation(
    run_id: str,
    criteria: ValidationCriteria,
    result: ValidationResult,
    evaluated_at: str,
    *,
    db_path: str,
    workspace: str,
) -> dict[str, Any]:
    """Persist a validation criteria + result via the Rust authority.

    Thin facade over `DeltaCoreClient.command("validation.register", ...)`.
    """
    from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

    try:
        client = DeltaCoreClient()
        return client.command(
            {
                "cmd": "validation.register",
                "db": db_path,
                "run_id": run_id,
                "criteria": criteria.to_dict(),
                "evaluated_at": evaluated_at,
                "result": result.to_dict(),
                "workspace": workspace,
            }
        )
    except DeltaCoreError as exc:
        raise ValidationAuthorityError(f"Rust validation register failed: {exc}") from exc


def get_validation(validation_id: str, *, db_path: str) -> dict[str, Any] | None:
    """Get a validation record by ID."""
    from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

    try:
        client = DeltaCoreClient()
        result = client.command(
            {
                "cmd": "validation.get",
                "db": db_path,
                "validation_id": validation_id,
            }
        )
        return result if result else None
    except DeltaCoreError as exc:
        raise ValidationAuthorityError(f"Rust validation get failed: {exc}") from exc


def list_validations(run_id: str | None = None, *, db_path: str) -> list[dict[str, Any]]:
    """List all validation records, optionally filtered by run_id."""
    from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

    try:
        client = DeltaCoreClient()
        payload = {"cmd": "validation.list", "db": db_path}
        if run_id is not None:
            payload["run_id"] = run_id
        result = client.command(payload)
        return result if isinstance(result, list) else []
    except DeltaCoreError as exc:
        raise ValidationAuthorityError(f"Rust validation list failed: {exc}") from exc


def latest_validation(run_id: str, *, db_path: str) -> dict[str, Any] | None:
    """Get the latest validation for a run_id."""
    from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

    try:
        client = DeltaCoreClient()
        result = client.command(
            {
                "cmd": "validation.latest",
                "db": db_path,
                "run_id": run_id,
            }
        )
        return result if result else None
    except DeltaCoreError as exc:
        raise ValidationAuthorityError(f"Rust validation latest failed: {exc}") from exc


def evaluate_and_register(
    run_id: str,
    criteria: ValidationCriteria,
    artifacts: list["Artifact"],
    *,
    db_path: str,
    workspace: str,
    valid_citation_count: int | None = None,
) -> tuple[dict[str, Any], ValidationResult]:
    """Evaluate criteria against artifacts and persist the result via Rust."""
    from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

    norm: list[dict[str, Any]] = []
    for a in artifacts:
        if isinstance(a, dict):
            norm.append(a)
        else:
            norm.append(a.to_dict())

    try:
        client = DeltaCoreClient()
        result = client.command(
            {
                "cmd": "validation.eval",
                "db": db_path,
                "run_id": run_id,
                "criteria": criteria.to_dict(),
                "artifacts": norm,
                "workspace": workspace,
                "valid_citation_count": valid_citation_count,
            }
        )
        record = result.get("record")
        result_dict = result.get("result")
        return record, ValidationResult.from_dict(result_dict)
    except DeltaCoreError as exc:
        raise ValidationAuthorityError(f"Rust validation eval failed: {exc}") from exc


__all__ = [
    "ValidationCriteria",
    "ValidationCheck",
    "ValidationResult",
    "DEFAULT_CRITERIA",
    "run_validation",
    "gate_status",
    "ValidationAuthorityError",
    "register_validation",
    "get_validation",
    "list_validations",
    "latest_validation",
    "evaluate_and_register",
]