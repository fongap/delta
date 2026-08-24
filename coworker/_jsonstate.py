"""Small JSON state-file helpers shared by the local state stores.

These stores back the sidecar with simple JSON files written on hot paths (every approval,
question, wake, inbound message, persona toggle, …). Two durability facts matter:

- A write interrupted by a crash / force-quit / power loss truncates the file. Writing to a
  temp file then atomically `os.replace`-ing it means the on-disk file is always complete.
- A load must never throw: a corrupt file (from a previous crash) must fall back to an empty
  state rather than raise `JSONDecodeError` and kill the sidecar at startup. The corrupt file
  is preserved as `.corrupt-<ts>` so the user can recover the data, not silently discarded.

`secrets.py` and `mcp/config.py` already do atomic tmp+replace; this centralises the same
pattern (plus the tolerant load) for the stores that did not.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def load_json_state(path: Path, default: Any = None) -> Any:
    """Read + parse `path`, returning `default` (and preserving the file) on any failure.

    Never raises: a missing file, an unreadable file, or a truncated/corrupt JSON all degrade
    to `default` (empty state), and the offending file is renamed to a sibling so a later
    edit doesn't overwrite the only copy of the user's data.
    """
    try:
        data = path.read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(data)
    except ValueError as exc:
        ts = time.strftime("%Y%m%d%H%M%S")
        backup = path.with_name(path.name + f".corrupt-{ts}")
        try:
            os.replace(path, backup)  # keep the bad bytes, not crash the app
        except OSError:
            pass
        return default


def save_json_state(path: Path, payload: Any) -> None:
    """Atomically write `payload` as JSON to `path` (tmp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
