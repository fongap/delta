"""ConversationStore persistence hardening: corrupt-line tolerance + atomic rewrites.

Read side: a corrupt line in a .jsonl must not brick session load. An append
interrupted mid-write (crash, full disk) leaves one malformed trailing line; load()
must skip it and return the recoverable history, not raise on every open.

Write side: `save()` appends new messages on the common path, but when a turn
*reduces* the message count (context compaction / summarization) it rewrites the
whole ``.jsonl``. That rewrite must be atomic: a crash partway through must not
truncate or erase the existing history — the most valuable data the app holds.

Semantic port of andrewyng/openworker
ccc845ded483056d5d5f1dfe4b725e8a93c2999d (corrupt JSONL tolerance) and
fbf57149faaa96f49c4fe8a42a5371906aee448e (atomic shrink rewrite).
"""

from __future__ import annotations

import json
import os

import pytest

from core.conversations import ConversationStore
from core.sessions import SessionRecord


def _seed(store: ConversationStore, sid: str, n: int) -> None:
    store.save(
        SessionRecord(
            session_id=sid,
            workspace="/tmp",
            model="m",
            mode="interactive",
            messages=[{"role": "user", "content": f"m{i}"} for i in range(n)],
        )
    )


# -- read side: corrupt-line tolerance -------------------------------------------


def test_load_skips_a_corrupt_trailing_line(tmp_path):
    store = ConversationStore(tmp_path / "state")
    sid = "abc123def456"
    _seed(store, sid, 2)

    # Simulate a torn write: append a truncated JSON line to the session's log.
    jsonl = tmp_path / "state" / "conversations" / f"{sid}.jsonl"
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": "unterm\n')  # no closing brace/quote

    loaded = store.load(sid)  # must not raise
    assert loaded is not None
    # The two good messages survive; the corrupt line is dropped.
    assert [m["content"] for m in loaded.messages] == ["m0", "m1"]


def test_load_skips_a_corrupt_middle_line(tmp_path):
    store = ConversationStore(tmp_path / "state")
    sid = "def456abc123"
    jsonl = tmp_path / "state" / "conversations" / f"{sid}.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text(
        '{"role": "user", "content": "first"}\n'
        "not json at all\n"
        '{"role": "assistant", "content": "third"}\n',
        encoding="utf-8",
    )
    # Register the session in the index so load() reaches the .jsonl.
    store._conn.execute(
        "INSERT INTO sessions (session_id, workspace, model, mode, title, n_msgs) "
        "VALUES (?, '/tmp', 'm', 'interactive', 't', 2)",
        (sid,),
    )
    store._conn.commit()

    loaded = store.load(sid)
    assert loaded is not None
    assert [m["content"] for m in loaded.messages] == ["first", "third"]


def test_revert_tolerates_a_corrupt_line(tmp_path):
    """revert() re-parses the log to slice it; a corrupt line must not make a user
    revert unrecoverable, and the surviving prefix must stay intact."""
    store = ConversationStore(tmp_path / "state")
    sid = "aaa111bbb222"
    _seed(store, sid, 3)
    jsonl = tmp_path / "state" / "conversations" / f"{sid}.jsonl"
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write("torn write\n")

    dropped = store.revert(sid, 2)
    assert [m["content"] for m in dropped] == ["m2"]
    assert [m["content"] for m in store.load(sid).messages] == ["m0", "m1"]


def _rec(sid: str, n: int) -> SessionRecord:
    return SessionRecord(
        session_id=sid,
        workspace="/w",
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": f"msg-{i}"} for i in range(n)],
    )


# -- write side: atomic rewrites ---------------------------------------------------


def test_shrink_rewrite_preserves_history_when_write_crashes(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    sid = "sess1"

    # Persist a 5-message history via the append path.
    store.save(_rec(sid, 5))
    assert len(store.load(sid).messages) == 5

    # Force a crash partway through the shrink rewrite (5 -> 2): the write fails after the
    # first line. A non-atomic in-place open(..., "w") truncates the real file at open() and
    # the crash then erases the history; an atomic tmp-then-replace leaves the original
    # untouched because the swap never happens.
    import core.conversations as conv

    real_open = open
    writes = {"n": 0}

    class _CrashingFile:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

        def write(self, s):
            writes["n"] += 1
            if writes["n"] >= 2:
                raise OSError("simulated crash mid-write")
            return self._fh.write(s)

    def crashing_open(file, mode="r", *args, **kwargs):
        fh = real_open(file, mode, *args, **kwargs)
        if "w" in mode and os.path.basename(str(file)).startswith(sid):
            return _CrashingFile(fh)
        return fh

    monkeypatch.setattr(conv, "open", crashing_open, raising=False)

    with pytest.raises(OSError):
        store.save(_rec(sid, 2))

    monkeypatch.undo()

    # The interrupted shrink must not have destroyed the existing history.
    assert len(store.load(sid).messages) == 5


def test_shrink_rewrite_persists_reduced_history(tmp_path):
    """The (non-crash) shrink path still rewrites the log to exactly the reduced set."""
    store = ConversationStore(tmp_path)
    sid = "sess2"

    store.save(_rec(sid, 5))
    assert len(store.load(sid).messages) == 5

    store.save(_rec(sid, 3))  # shrink 5 -> 3
    reloaded = store.load(sid)
    assert [m["content"] for m in reloaded.messages] == ["msg-0", "msg-1", "msg-2"]

    # No leftover temp file next to the conversation log.
    assert not (tmp_path / "conversations" / f"{sid}.tmp").exists()


def test_revert_preserves_history_when_write_crashes(tmp_path, monkeypatch):
    """revert() is the other explicit rewrite; a crash mid-rewrite must leave the
    original file intact, not a truncated one."""
    store = ConversationStore(tmp_path)
    sid = "sess3"
    _seed(store, sid, 4)

    import core.conversations as conv

    real_open = open
    writes = {"n": 0}

    class _CrashingFile:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

        def write(self, s):
            writes["n"] += 1
            if writes["n"] >= 2:
                raise OSError("simulated crash mid-write")
            return self._fh.write(s)

    def crashing_open(file, mode="r", *args, **kwargs):
        fh = real_open(file, mode, *args, **kwargs)
        if "w" in mode and os.path.basename(str(file)).startswith(sid):
            return _CrashingFile(fh)
        return fh

    monkeypatch.setattr(conv, "open", crashing_open, raising=False)

    with pytest.raises(OSError):
        store.revert(sid, 2)

    monkeypatch.undo()

    assert len(store.load(sid).messages) == 4
    assert not (tmp_path / "conversations" / f"{sid}.tmp").exists()


# -- full lifecycle: repair/corrupt → load → save → reload ------------------------

def _assistant_with_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "run_shell", "arguments": "{}"},
            }
        ],
    }


def _reload_fresh(tmp_path, sid: str):
    """A brand-new store instance — a cold `_known` cache, like a process restart.
    The append-offset bugs only bite when `_count()` reads the disk fresh."""
    return ConversationStore(tmp_path / "state")


def _jsonl_path(tmp_path, sid: str):
    return tmp_path / "state" / "conversations" / f"{sid}.jsonl"


def test_corrupt_line_then_continue_persists_new_message(tmp_path):
    """P0 regression: after tolerating a corrupt line, the next save() must still
    persist new messages. `_count()` used to count physical lines, so a skipped
    corrupt line made `len(record.messages) == existing` and the turn's new
    messages were silently never written."""
    store = ConversationStore(tmp_path / "state")
    sid = "corruptcontinue01"
    _seed(store, sid, 2)
    with open(_jsonl_path(tmp_path, sid), "a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": "torn\n')  # corrupt line

    loaded = _reload_fresh(tmp_path, sid).load(sid)
    assert [m["content"] for m in loaded.messages] == ["m0", "m1"]

    loaded.messages.append({"role": "user", "content": "after restart"})
    _reload_fresh(tmp_path, sid).save(loaded)

    reloaded = _reload_fresh(tmp_path, sid).load(sid)
    assert [m["content"] for m in reloaded.messages] == ["m0", "m1", "after restart"]


def test_corrupt_line_canonicalized_on_load(tmp_path):
    """The corrupt line itself must not survive: load() rewrites the canonical
    history, so the store converges instead of carrying damage forever."""
    store = ConversationStore(tmp_path / "state")
    sid = "corruptrewrite01"
    _seed(store, sid, 2)
    with open(_jsonl_path(tmp_path, sid), "a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": "torn\n')

    _reload_fresh(tmp_path, sid).load(sid)

    text = _jsonl_path(tmp_path, sid).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # every remaining line is valid JSON


def test_repaired_placeholder_persists_without_duplicating_tail(tmp_path):
    """P0 regression: a repaired history that grew a placeholder used to collide
    with `save()`'s disk-line offset — the placeholder was never written and the
    last real message was appended a second time on every subsequent turn."""
    store = ConversationStore(tmp_path / "state")
    sid = "pairingoffset01"
    _seed(store, sid, 1)  # create the index row; history is overwritten below
    disk_history = [
        {"role": "user", "content": "question"},
        _assistant_with_call("c1"),
        {"role": "user", "content": "follow-up"},
    ]
    with open(_jsonl_path(tmp_path, sid), "w", encoding="utf-8") as f:
        for m in disk_history:
            f.write(json.dumps(m) + "\n")

    loaded = _reload_fresh(tmp_path, sid).load(sid)
    # load() repaired in memory: placeholder injected before the follow-up.
    assert [m["role"] for m in loaded.messages] == [
        "user", "assistant", "tool", "user",
    ]

    loaded.messages.append({"role": "assistant", "content": "answer"})
    _reload_fresh(tmp_path, sid).save(loaded)

    reloaded = _reload_fresh(tmp_path, sid).load(sid)
    assert [m["role"] for m in reloaded.messages] == [
        "user", "assistant", "tool", "user", "assistant",
    ]
    assert [m["content"] for m in reloaded.messages if m["role"] == "user"] == [
        "question", "follow-up",
    ]
    # And the placeholder is durable now, not re-synthesised per load.
    tool_msgs = [m for m in reloaded.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"


def test_lifecycle_is_idempotent_across_restart_cycles(tmp_path):
    """load → save → reload repeatedly with one new message per cycle: history
    must grow by exactly one message per cycle, forever — the canonical file and
    the in-memory repair must reach a fixed point."""
    sid = "lifecyclefixedpt1"
    store = ConversationStore(tmp_path / "state")
    record = store.load(sid) or SessionRecord(
        session_id=sid,
        workspace="/tmp",
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "m0"}],
    )
    store.save(record)

    for cycle in range(1, 4):
        store = _reload_fresh(tmp_path, sid)
        record = store.load(sid)
        assert record is not None
        record.messages.append(
            {"role": "user", "content": f"m{cycle}"}
        )
        _reload_fresh(tmp_path, sid).save(record)

        store = _reload_fresh(tmp_path, sid)
        record = store.load(sid)
        assert [m["content"] for m in record.messages] == [
            f"m{i}" for i in range(cycle + 1)
        ]
        row = store._conn.execute(
            "SELECT n_msgs FROM sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row["n_msgs"] == len(record.messages)
