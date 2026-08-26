"""Make every SessionManager mixin inherit ManagerBase (idempotent, UTF-8 safe)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"D:\900 AIWork\910 GitHub\FongHub\delta\coworker\server")
MIXIN_CLASSES = {
    "manager_workspace.py": "WorkspaceTrustMixin",
    "manager_sessions.py": "SessionsMixin",
    "manager_events.py": "EventsMixin",
    "manager_mcp_connectors.py": "McpConnectorsMixin",
    "manager_connections.py": "ConnectionsMixin",
    "manager_inbox.py": "InboxApprovalsMixin",
    "manager_gateway.py": "GatewayInboundMixin",
    "manager_automations.py": "AutomationsMixin",
    "manager_artifacts.py": "ArtifactsBrowserAuditMixin",
    "manager_providers.py": "ProvidersSettingsMixin",
    "manager_skills_memory.py": "SkillsMemoryMixin",
}
IMPORT_LINE = "from .manager_base import ManagerBase\n"

for fname, cls in MIXIN_CLASSES.items():
    p = ROOT / fname
    text = p.read_text(encoding="utf-8")
    changed = False
    if "from .manager_base import ManagerBase" not in text:
        # insert after the `from __future__ import annotations` line
        anchor = "from __future__ import annotations\n"
        i = text.index(anchor) + len(anchor)
        text = text[:i] + "\n" + IMPORT_LINE + text[i:]
        changed = True
    old = f"class {cls}:"
    new = f"class {cls}(ManagerBase):"
    if old in text:
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        p.write_text(text, encoding="utf-8", newline="\n" if "\r\n" not in text[:500] else None)
        print(f"{fname}: updated")
    else:
        print(f"{fname}: nothing to do")
