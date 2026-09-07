#!/usr/bin/env python3
"""Rust Core smoke gate — verify the delta_core binary works end-to-end.

R1 (P1-G) CI gate: a packaged Rust Core binary must be able to:

  1. Process a ping.
  2. Append a ledger event with a continuous hash chain.
  3. Run a full idem state cycle (record_planned → mark_executing → commit).
  4. Save / delete a task and add a run.

This script is the "Package Rust Core end-to-end smoke gate" the R1
instructions require. The CI job ``rust-core-smoke`` invokes it
after building ``delta_core`` in release mode; a non-zero exit
fails the gate.

Usage::

    python scripts/check_rust_core_smoke.py
    python scripts/check_rust_core_smoke.py --binary path/to/delta_core.exe

Exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO / "core" / "runtime-native"


def find_binary(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            print(f"error: binary not found: {path}", file=sys.stderr)
            sys.exit(2)
        return path
    for profile in ("release", "debug"):
        target = "delta_core.exe" if sys.platform == "win32" else "delta_core"
        candidate = CRATE_DIR / "target" / profile / target
        if candidate.exists():
            return candidate
    print(
        "error: delta_core binary not built. Run: cargo build --bin delta_core",
        file=sys.stderr,
    )
    sys.exit(2)


def send(proc: subprocess.Popen, payload: dict) -> dict:
    line = json.dumps(payload)
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    assert proc.stdout is not None
    response = proc.stdout.readline()
    if not response:
        raise RuntimeError("delta_core closed stdout (crash?)")
    parsed = json.loads(response)
    if not parsed.get("ok"):
        raise RuntimeError(
            f"delta_core {payload.get('cmd')} failed: {parsed.get('error')}"
        )
    return parsed["result"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default=None)
    args = parser.parse_args()

    binary = find_binary(args.binary)

    with tempfile.TemporaryDirectory() as tmp:
        ledger_db = str(Path(tmp) / "ledger.db")
        idem_db = str(Path(tmp) / "idem.db")
        task_db = str(Path(tmp) / "tasks.db")

        proc = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        try:
            # 1. ping
            r = send(proc, {"cmd": "ping"})
            assert r == {"pong": True}, f"ping returned {r}"

            # 2. ledger hash chain
            r1 = send(
                proc,
                {
                    "cmd": "ledger.append",
                    "db": ledger_db,
                    "run_id": "r1",
                    "type": "run.started",
                    "actor": "user",
                    "payload": {"kind": "run"},
                },
            )
            r2 = send(
                proc,
                {
                    "cmd": "ledger.append",
                    "db": ledger_db,
                    "run_id": "r1",
                    "type": "run.completed",
                    "actor": "system",
                },
            )
            assert r1["seq"] == 1
            assert r2["seq"] == 2
            assert r2["prev_hash"] == r1["hash"], (
                "hash chain broken: r2.prev_hash != r1.hash"
            )

            # 3. idem full cycle
            r = send(
                proc,
                {
                    "cmd": "idem.record_planned",
                    "db": idem_db,
                    "run_id": "r1",
                    "tool_call_id": "tc1",
                    "tool_name": "write_file",
                    "args": {"path": "out.md"},
                },
            )
            assert "operation_id" in r
            send(
                proc,
                {
                    "cmd": "idem.mark_executing",
                    "db": idem_db,
                    "run_id": "r1",
                    "tool_call_id": "tc1",
                },
            )
            send(
                proc,
                {
                    "cmd": "idem.commit",
                    "db": idem_db,
                    "run_id": "r1",
                    "tool_call_id": "tc1",
                    "tool_name": "write_file",
                    "args": {"path": "out.md"},
                    "result": {"ok": True},
                },
            )

            # 4. task save + add_run + delete
            send(
                proc,
                {
                    "cmd": "task.save",
                    "db": task_db,
                    "task_id": "t1",
                    "enabled": True,
                    "next_run": None,
                    "data": "{}",
                },
            )
            send(
                proc,
                {
                    "cmd": "task.add_run",
                    "db": task_db,
                    "run_id": "r1",
                    "task_id": "t1",
                    "started_at": 1700000000.0,
                    "data": "{}",
                    "workspace": "",
                },
            )
            send(
                proc,
                {
                    "cmd": "task.delete",
                    "db": task_db,
                    "task_id": "t1",
                },
            )

        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    print(f"rust-core-smoke: all checks passed (binary={binary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
