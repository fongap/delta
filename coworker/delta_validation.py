"""Hard local validation for Delta deliverables."""

from __future__ import annotations

from pathlib import Path


def validate_artifact_path(workspace: str | Path, artifact: str | Path) -> dict[str, object]:
    root = Path(workspace).expanduser().resolve()
    target = Path(artifact).expanduser()
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return {"ok": False, "errors": ["Artifact is outside the task workspace."]}
    if not target.is_file():
        return {"ok": False, "errors": ["Artifact file does not exist."]}
    return {"ok": True, "path": str(target.relative_to(root)), "size": target.stat().st_size}
