import json
import sys
from pathlib import Path

import httpx

sec = json.loads(Path(r"C:\AgentHub\Delta\Data\secrets.json").read_text(encoding="utf-8"))
BASE, KEY = sec["provider:FongAI"]["base_url"].rstrip("/"), sec["provider:FongAI"]["api_key"]


def stream_probe(model, prompt, tag):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    n, acc, finish, saw_done = 0, "", None, False
    try:
        with httpx.Client(timeout=60) as client:
            with client.stream(
                "POST",
                f"{BASE}/chat/completions",
                headers={"Authorization": f"Bearer {KEY}"},
                json=payload,
            ) as resp:
                code = resp.status_code
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        saw_done = True
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    n += 1
                    ch = (obj.get("choices") or [{}])[0]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        acc += piece
    except Exception as exc:
        print(f"[{tag}] EXC: {exc}")
        return
    print(f"[{tag}] http={code} events={n} finish={finish} done={saw_done} chars={len(acc)} first={acc[:20]!r}")


def nonstream_probe(model, prompt, tag):
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
    try:
        r = httpx.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json=payload,
            timeout=60,
        )
        body = r.json()
        msg = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        finish = ((body.get("choices") or [{}])[0]).get("finish_reason")
        print(f"[{tag}] http={r.status_code} finish={finish} chars={len(msg or '')} first={(msg or '')[:20]!r}")
    except Exception as exc:
        print(f"[{tag}] EXC: {exc}")


stream_probe("max", "请用100字左右介绍你自己", "stream max/100字")
stream_probe("max", "hi", "stream max/hi")
stream_probe("pro", "请用100字左右介绍你自己", "stream pro/100字")
stream_probe("ultra", "hi", "stream ultra/hi")
nonstream_probe("max", "请用100字左右介绍你自己", "nonstream max/100字")
nonstream_probe("pro", "hi", "nonstream pro/hi")
