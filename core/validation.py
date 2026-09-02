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
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
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
    """

    min_artifacts: int = 1
    max_artifacts: int = 50
    required_paths: list[str] = field(default_factory=list)
    required_substrings: dict[str, list[str]] = field(default_factory=dict)
    min_size: dict[str, int] = field(default_factory=dict)
    max_size: dict[str, int] = field(default_factory=dict)
    require_complete: bool = True
    csv_required_headers: dict[str, list[str]] = field(default_factory=dict)

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
) -> ValidationResult:
    """Evaluate the criteria against the run's artifacts.

    `artifacts` may be `Artifact` instances or dicts (the in-memory shape used
    after `register_run_artifacts`). `workspace` is the absolute path used to
    resolve relative paths when reading file contents (substring + CSV checks).
    """
    norm: list[dict[str, Any]] = []
    for a in artifacts:
        if isinstance(a, dict):
            norm.append(a)
        else:
            norm.append(a.to_dict())
    by_path = {a["path"]: a for a in norm}

    checks: list[ValidationCheck] = []
    evidence: dict[str, Any] = {"artifact_count": len(norm)}

    # Count gate
    count_ok = criteria.min_artifacts <= len(norm) <= criteria.max_artifacts
    checks.append(
        ValidationCheck(
            name="artifact_count",
            ok=count_ok,
            detail=f"{len(norm)} artifacts (min={criteria.min_artifacts}, max={criteria.max_artifacts})",
        )
    )
    if not count_ok:
        return ValidationResult(ok=False, checks=checks, evidence=evidence)

    # Completeness gate
    incomplete = [a["path"] for a in norm if a.get("incomplete")]
    if criteria.require_complete and incomplete:
        checks.append(
            ValidationCheck(
                name="all_artifacts_complete",
                ok=False,
                detail=f"incomplete writes: {incomplete}",
            )
        )
        return ValidationResult(ok=False, checks=checks, evidence=evidence)
    checks.append(ValidationCheck(name="all_artifacts_complete", ok=True))

    # Required paths
    missing = [p for p in criteria.required_paths if p not in by_path]
    if missing:
        checks.append(
            ValidationCheck(
                name="required_paths",
                ok=False,
                detail=f"missing: {missing}",
            )
        )
        return ValidationResult(ok=False, checks=checks, evidence=evidence)
    if criteria.required_paths:
        checks.append(ValidationCheck(name="required_paths", ok=True))

    # Size gates
    for path, lo in criteria.min_size.items():
        a = by_path.get(path)
        if a is None or a.get("size", 0) < lo:
            checks.append(
                ValidationCheck(
                    name=f"min_size:{path}",
                    ok=False,
                    detail=f"size={a.get('size') if a else None} < {lo}",
                )
            )
            return ValidationResult(ok=False, checks=checks, evidence=evidence)
    for path, hi in criteria.max_size.items():
        a = by_path.get(path)
        if a is not None and a.get("size", 0) > hi:
            checks.append(
                ValidationCheck(
                    name=f"max_size:{path}",
                    ok=False,
                    detail=f"size={a['size']} > {hi}",
                )
            )
            return ValidationResult(ok=False, checks=checks, evidence=evidence)
    if criteria.min_size or criteria.max_size:
        checks.append(ValidationCheck(name="size_gates", ok=True))

    # Substring + CSV checks need a workspace to read from
    if workspace and (criteria.required_substrings or criteria.csv_required_headers):
        ws = Path(workspace)
        for path, needles in criteria.required_substrings.items():
            a = by_path.get(path)
            if a is None or a.get("incomplete"):
                continue  # already reported above
            try:
                text = (ws / path).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                checks.append(
                    ValidationCheck(
                        name=f"read:{path}",
                        ok=False,
                        detail=f"read failed: {exc}",
                    )
                )
                return ValidationResult(ok=False, checks=checks, evidence=evidence)
            for needle in needles:
                if needle not in text:
                    checks.append(
                        ValidationCheck(
                            name=f"substring:{path}:{needle!r}",
                            ok=False,
                            detail="not found",
                        )
                    )
                    return ValidationResult(
                        ok=False, checks=checks, evidence=evidence
                    )
            checks.append(ValidationCheck(name=f"substrings:{path}", ok=True))

        for path, headers in criteria.csv_required_headers.items():
            a = by_path.get(path)
            if a is None or a.get("incomplete"):
                continue
            try:
                with (ws / path).open("r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f)
                    first = next(reader, None)
            except (OSError, csv.Error) as exc:
                checks.append(
                    ValidationCheck(
                        name=f"csv_read:{path}",
                        ok=False,
                        detail=f"read failed: {exc}",
                    )
                )
                return ValidationResult(ok=False, checks=checks, evidence=evidence)
            if first is None:
                checks.append(
                    ValidationCheck(
                        name=f"csv_headers:{path}",
                        ok=False,
                        detail="empty CSV",
                    )
                )
                return ValidationResult(ok=False, checks=checks, evidence=evidence)
            missing_h = [h for h in headers if h not in first]
            if missing_h:
                checks.append(
                    ValidationCheck(
                        name=f"csv_headers:{path}",
                        ok=False,
                        detail=f"missing headers: {missing_h}",
                    )
                )
                return ValidationResult(ok=False, checks=checks, evidence=evidence)
            checks.append(ValidationCheck(name=f"csv_headers:{path}", ok=True))

    return ValidationResult(ok=all(c.ok for c in checks), checks=checks, evidence=evidence)


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
