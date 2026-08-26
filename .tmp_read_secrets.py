import json
from pathlib import Path

sec = json.loads(Path(r"C:\AgentHub\Delta\Data\secrets.json").read_text(encoding="utf-8"))
# structure may be {profiles: {...}} or flat; find provider:custom / fongai entries
def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]")
    else:
        s = str(obj)
        shown = "<len:%d>" % len(s) if any(t in path.lower() for t in ("key", "token", "secret")) else s[:60]
        print(f"{path} = {shown}")

walk(sec)
