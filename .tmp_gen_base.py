"""Generate coworker/server/manager_base.py (v2 — multiline-signature aware)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(r"D:\900 AIWork\910 GitHub\FongHub\delta\coworker\server")
MIXINS = [
    "manager_workspace.py",
    "manager_sessions.py",
    "manager_events.py",
    "manager_mcp_connectors.py",
    "manager_connections.py",
    "manager_inbox.py",
    "manager_gateway.py",
    "manager_automations.py",
    "manager_artifacts.py",
    "manager_providers.py",
    "manager_skills_memory.py",
]
ALL_FILES = MIXINS + ["manager.py", "manager_support.py"]

assign_re = re.compile(r"self\.(\w+)")
accessed: set[str] = set()
for fname in ALL_FILES:
    accessed.update(assign_re.findall((ROOT / fname).read_text(encoding="utf-8")))


def is_method(name: str) -> bool:
    pat = f"def {name}("
    for fname in ALL_FILES:
        text = (ROOT / fname).read_text(encoding="utf-8")
        start = 0
        while True:
            i = text.find(pat, start)
            if i < 0:
                break
            if re.search(r"\bself\b", text[i : i + 160]):
                return True
            start = i + 1
    return False


methods = sorted(n for n in accessed if is_method(n))
data_attrs = sorted(accessed - set(methods))
method_names = methods

lines: list[str] = []
lines.append('"""Type-declaration surface shared by the SessionManager mixin modules.\n')
lines.append(
    """SessionManager is composed from ~12 plain mixin classes (see manager.py). Each
mixin reaches into state and helpers that live on the *composed* object — stores,
runtime ports, gateway helpers, autotitle bookkeeping — which a checker cannot see
from the mixin alone. This module declares that shared surface once: class-level
ANNOTATIONS only (no values), so instances gain nothing at runtime and the MRO
still resolves every real definition on the concrete mixins first.

Maintenance: when a mixin starts using a new `self.<member>`, add it here.
Types are deliberately loose (Any / Callable[..., Any]); tighten individual
members as needed."""
)
lines.append('"""\n')
lines.append("from __future__ import annotations\n")
lines.append("from typing import Any, Callable\n\n\n")
lines.append("class ManagerBase:\n")
for name in data_attrs:
    lines.append(f"    {name}: Any\n")
if data_attrs and method_names:
    lines.append("\n")
for name in method_names:
    lines.append(f"    {name}: Callable[..., Any]\n")

out = ROOT / "manager_base.py"
out.write_text("".join(lines), encoding="utf-8", newline="\n")
print(f"wrote {out.name}: {len(data_attrs)} data attrs, {len(method_names)} methods")
print("methods:", ", ".join(methods))
