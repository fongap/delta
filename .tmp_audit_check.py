import sqlite3
import json
import sys

db = sqlite3.connect(r"C:\AgentHub\Delta\Data\coworker.db")
db.row_factory = sqlite3.Row

print("=== 最近 12 条审计事件 ===")
for r in db.execute(
    "SELECT timestamp, tool, stage, status, level, reason, result_preview "
    "FROM audit_events ORDER BY id DESC LIMIT 12"
):
    print(
        f"{r['timestamp']} | {r['tool']} | {r['stage']} | {r['status']} | "
        f"L={r['level']} | {(r['reason'] or '')[:80]} | {(r['result_preview'] or '')[:60]}"
    )

print()
print("=== 最近会话的最后几条消息 ===")
sid = sys.argv[1] if len(sys.argv) > 1 else None
q = (
    "SELECT session_id, title, updated_at, messages FROM sessions "
    + (f"WHERE session_id = ? " if sid else "")
    + "ORDER BY updated_at DESC LIMIT 1"
)
for r in db.execute(q, (sid,) if sid else ()):
    print(f"session={r['session_id']} title={r['title']} updated={r['updated_at']}")
    msgs = json.loads(r["messages"] or "[]")
    for m in msgs[-8:]:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text", ""))[:80] for p in content if isinstance(p, dict)
            )
        text = str(content or "")[:150].replace("\n", "\\n")
        extra = ""
        if m.get("reasoning"):
            extra = f" [reasoning {len(str(m['reasoning']))} chars]"
        print(f"  [{role}] {text}{extra}")
