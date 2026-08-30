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
