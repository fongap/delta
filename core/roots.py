"""Read/write roots granted to a controlled capability worker.

Rust policy validates and issues these grants. Python consumes the immutable job scope
only to resolve worker file operations; it is not an application policy authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class RootDir:
    path: Path
    writable: bool = False
    label: str = ""  # display name; defaults to the dir's basename

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser().resolve()
        if not self.label:
            self.label = self.path.name or str(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "writable": self.writable, "label": self.label}


def normalize_roots(roots: Iterable[Any] | None) -> list[RootDir]:
    """Coerce a mixed list (RootDir | dict{path,writable,label} | str/Path) into RootDirs.
    Bare str/Path entries are treated as read-only; pass dicts/RootDirs to grant write.
    """
    out: list[RootDir] = []
    for r in roots or []:
        if isinstance(r, RootDir):
            out.append(r)
        elif isinstance(r, dict):
            out.append(
                RootDir(
                    path=r["path"],
                    writable=bool(r.get("writable", False)),
                    label=r.get("label", ""),
                )
            )
        elif isinstance(r, (str, Path)):
            out.append(RootDir(path=Path(r), writable=False))
        else:  # duck-typed object with .path/.writable
            out.append(
                RootDir(
                    path=getattr(r, "path"),
                    writable=bool(getattr(r, "writable", False)),
                )
            )
    return out


def render_context(roots: list[RootDir]) -> str:
    """The `<system-context>` body listing the dirs available this turn. Empty when no roots."""
    if not roots:
        return ""
    lines = ["Available directories (you may use file/shell tools within these):"]
    for i, r in enumerate(roots):
        access = "read-write" if r.writable else "read-only"
        tag = " — primary scratch, the default place to save files" if i == 0 else ""
        lines.append(f"- {r.path} [{access}]{tag}")
    lines.append(
        "Relative paths resolve against the primary directory; pass an absolute path to use "
        "another directory. Writes are only allowed in read-write directories. If the user "
        "cares where a deliverable lands, ask; otherwise save it in the primary scratch."
    )
    return "\n".join(lines)
