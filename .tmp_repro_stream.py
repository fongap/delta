"""Reproduce the truncated-response issue against the user's custom provider.

Sends the SAME streaming request Delta sends (openai chat completions, stream=true)
and dumps every SSE event so we can see exactly where the stream stops.
The API key is read from the app's secret store and NEVER printed.
"""
import json
import sys
from pathlib import Path

import httpx

sec = json.loads(Path(r"C:\AgentHub\Delta\Data\secrets.json").read_text(encoding="utf-8"))
prof = sec["provider:FongAI"]
BASE, KEY = prof["base_url"].rstrip("/"), prof["api_key"]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "max"

payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "You are Delta, a helpful assistant."},
        {"role": "user", "content": "请用100字左右介绍你自己"},
    ],
    "stream": True,
    "stream_options": {"include_usage": True},
}

acc = ""
n_events = 0
finish = None
usage = None
saw_done = False
with httpx.Client(timeout=120) as client:
    with client.stream(
        "POST",
        f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json=payload,
    ) as resp:
        print(f"HTTP {resp.status_code} | content-type: {resp.headers.get('content-type')}")
        if resp.status_code != 200:
            print(resp.read().decode("utf-8", "replace")[:500])
            sys.exit(1)
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                if line.strip():
                    print(f"  non-data line: {line[:80]!r}")
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                print("  [DONE] received")
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError as exc:
                print(f"  BAD JSON: {data[:100]!r} ({exc})")
                continue
            n_events += 1
            ch = (obj.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            piece = delta.get("content")
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            if obj.get("usage"):
                usage = obj["usage"]
            if piece:
                acc += piece
                print(f"  #{n_events}: content[{len(piece)}] {piece!r}")
            elif reasoning:
                print(f"  #{n_events}: reasoning[{len(reasoning)}] {reasoning[:40]!r}")
            elif finish:
                print(f"  #{n_events}: finish_reason={finish}")
            else:
                print(f"  #{n_events}: (other keys: {list(obj.keys())} / delta keys: {list(delta.keys())})")

print("---")
print(f"events={n_events} finish={finish} [DONE]={saw_done} usage={json.dumps(usage) if usage else None}")
print(f"accumulated content ({len(acc)} chars): {acc!r}")
