"""ConversationStore — global, file-backed session storage shared by all surfaces.

Layout under a base dir (default `~/.config/delta/`):
  core.db                  SQLite index: sessions(id → project, title, n_msgs), workspaces, memory
  conversations/<id>.jsonl     append-only message log, one file per conversation

Writes append only the new messages each turn (no rewriting history). Legacy rows that
stored messages inline are lazily migrated to a .jsonl on first load/save.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from core.sessions import SessionRecord

# A session id becomes a filename (`<id>.jsonl`), so it must be a single, benign path
# component. Every legitimate id Delta generates is hex or a `__run__`/`__task__`-prefixed
# hex string, so this charset is a superset of what we generate; it excludes the path
# separators and dots a client-supplied id would need to escape the store. Session ids
# arrive from client-controlled surfaces (gateway session routes, REST paths), so without
# this an id like `../../evil` would write `<base>/evil.jsonl` outside `conversations/`.
_SAFE_SESSION_ID = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def is_safe_session_id(sid: str) -> bool:
    return bool(isinstance(sid, str) and _SAFE_SESSION_ID.match(sid))


def _load_roots(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _load_grants(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _display_title(row: sqlite3.Row) -> str | None:
    """Title precedence for every read path: a manual rename (renamed=1) always wins,
    then the generated auto_title, then the first-line snapshot `save()` wrote."""
    if row["renamed"]:
        return row["title"]
    return row["auto_title"] or row["title"]


def _parse_jsonl(text: str) -> tuple[list[dict], int]:
    """Parse a .jsonl body tolerantly: skip blank lines and, rather than failing the
    whole load, skip individual corrupt/truncated lines. An append interrupted mid-write
    (crash, disk full) leaves one malformed line; a bare `json.loads` would raise and
    make load() throw on every open — bricking that session on every surface. Keep the
    recoverable history instead. Returns the valid messages and how many corrupt lines
    were dropped (blank lines don't count) — the caller canonicalizes when that is > 0."""
    messages: list[dict] = []
    corrupt = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            corrupt += 1
    return messages, corrupt


def title_from(messages: list[dict]) -> str:
    from core.attachments import content_to_text

    for m in messages:
        if m.get("role") == "user":
            text = content_to_text(m.get("content"), image_placeholder="").strip()
            if text:
                return text.splitlines()[0][:60]
    return "New session"


class ConversationStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        self.conv_dir = self.base / "conversations"
        self.conv_dir.mkdir(exist_ok=True)
        self.db_path = self.base / "core.db"

        self._lock = threading.RLock()
        # Cached on-disk line count per session, so the attach-only JSONL isn't re-read in
        # full on every save (the checkpoint path runs on the event loop; _count was O(history)
        # and grew per turn). In sync because every write to a .jsonl goes through this class.
        self._known: dict[str, int] = {}
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, mode TEXT,
                title TEXT, agent TEXT DEFAULT 'code', n_msgs INTEGER DEFAULT 0, messages TEXT,
                extra_roots TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
                origin TEXT, origin_label TEXT,
                auto_title TEXT, renamed INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                path TEXT PRIMARY KEY, last_used TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
        for ddl in (
            "ALTER TABLE sessions ADD COLUMN title TEXT",
            "ALTER TABLE sessions ADD COLUMN n_msgs INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN agent TEXT DEFAULT 'code'",
            "ALTER TABLE sessions ADD COLUMN extra_roots TEXT",
            "ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN archived INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN origin TEXT",
            "ALTER TABLE sessions ADD COLUMN origin_label TEXT",
            "ALTER TABLE sessions ADD COLUMN auto_title TEXT",
            "ALTER TABLE sessions ADD COLUMN renamed INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN grants TEXT",
            "ALTER TABLE sessions ADD COLUMN compaction TEXT",
            "ALTER TABLE sessions ADD COLUMN reasoning_effort TEXT DEFAULT 'auto'",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.commit()
        self._backfill_counts()

    # -- file helpers -----------------------------------------------------------
    def _file(self, sid: str) -> Path:
        # Single chokepoint for every conversation-file path. Reject ids that aren't a
        # safe path component, then confirm the resolved path stays inside conv_dir — so
        # a crafted id can never read or clobber a file outside the store.
        if not is_safe_session_id(sid):
            raise ValueError(f"unsafe session id: {sid!r}")
        path = (self.conv_dir / f"{sid}.jsonl").resolve()
        if path.parent != self.conv_dir.resolve():
            raise ValueError(f"unsafe session id: {sid!r}")
        return path

    def _read_jsonl(self, sid: str) -> list[dict] | None:
        messages, _ = self._read_canonical(sid, repair=False)
        return messages

    def _read_canonical(
        self, sid: str, *, repair: bool = True
    ) -> tuple[list[dict] | None, bool]:
        """Read the session's .jsonl and return its valid messages plus whether the
        on-disk bytes drifted from that canonical form — corrupt lines dropped, or the
        tool-pairing repair changed anything. Drift means the file must be rewritten
        before any further append: `save()` offsets new messages by the on-disk message
        count, so a disk that disagrees with the repaired in-memory history would
        mis-align every future append (duplicated tail messages, lost placeholders)."""
        path = self._file(sid)
        if not path.exists():
            return None, False
        text = path.read_text(encoding="utf-8")
        messages, corrupt = _parse_jsonl(text)
        drifted = corrupt > 0
        if repair and messages:
            repaired = self._repair_tool_pairing(messages)
            if repaired != messages:
                messages = repaired
                drifted = True
        return messages, drifted

    # -- tool-call/result pairing repair ----------------------------------------
    @staticmethod
    def _repair_tool_pairing(messages: list[dict]) -> list[dict]:
        """Reorder messages so every tool result immediately follows its call.

        Append-only persistence means an interrupted turn can leave a user message
        between an assistant ``tool_calls`` block and the matching ``tool`` result.
        Providers reject that ordering (Anthropic 400/2013, OpenAI "tool_call_ids did
        not have response messages"), making the session permanently unrecoverable.

        This pass:
        * Moves a real ``tool`` result found later in the thread to sit right after its
          call.
        * Synthesises a placeholder result for a call with no matching tool message —
          but **only** when the thread has moved past the call (i.e. there are messages
          after the assistant block). A trailing assistant ``tool_calls`` with no result
          is a pending/interrupted call that the engine will resume; injecting a
          placeholder there would break durable resume.
        * Is idempotent — a well-formed thread passes through unchanged.
        """
        if not messages:
            return messages

        # Collect tool_call ids from assistant messages.
        pending_calls: dict[str, int] = {}  # call_id → index of the assistant msg
        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    call_id = tc.get("id")
                    if call_id:
                        pending_calls[call_id] = i

        if not pending_calls:
            return messages  # no tool calls at all

        # Find tool results and where they sit relative to their calls.
        # call_id → index of the tool result message (first one wins)
        found_results: dict[str, int] = {}
        for i, m in enumerate(messages):
            if m.get("role") == "tool":
                call_id = m.get("tool_call_id")
                if call_id and call_id in pending_calls and call_id not in found_results:
                    found_results[call_id] = i

        # A trailing assistant block (the last message in the thread) holds pending
        # calls the engine will resume — never inject placeholders for those.
        last_msg_idx = len(messages) - 1
        trailing_calls = {
            call_id
            for call_id, call_idx in pending_calls.items()
            if call_idx == last_msg_idx
        }

        # Only act when a result is missing or not immediately after its call.
        needs_repair = False
        for call_id, call_idx in pending_calls.items():
            if call_id in trailing_calls and call_id not in found_results:
                continue  # pending call — engine will resume
            if call_id in found_results:
                if found_results[call_id] != call_idx + 1:
                    needs_repair = True  # result exists but not immediately after
            else:
                needs_repair = True  # no result at all
        if not needs_repair:
            return messages  # already well-formed (or only pending calls)

        # Rebuild: after each assistant block emit its tool results, in call order —
        # moved from their original position, or synthesised when lost.
        consumed_result_indices: set[int] = set()
        repaired: list[dict] = []

        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                repaired.append(m)
                for tc in m["tool_calls"]:
                    call_id = tc.get("id")
                    if not call_id:
                        continue
                    if call_id in found_results:
                        result_idx = found_results[call_id]
                        if result_idx not in consumed_result_indices:
                            repaired.append(messages[result_idx])
                            consumed_result_indices.add(result_idx)
                    elif call_id not in trailing_calls:
                        repaired.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": (
                                '{"error": "tool result was lost during an '
                                'interrupted turn"}'
                            ),
                        })
            elif i in consumed_result_indices:
                continue  # already moved this tool result up
            else:
                repaired.append(m)

        return repaired

    def _count(self, sid: str) -> int:
        """Canonical message count for `sid`, using the cache when it is warm.

        The JSONL is append-only and only ever written by this class (append / revert / the
        rare rewrite / load-time canonicalization), so the cached count stays accurate
        without a full file read. The cold path counts *valid messages*, not physical
        lines — `save()` offsets appends by this number against `len(record.messages)`,
        so a corrupt line counted as a message would make that offset land one short and
        silently drop the turn's new messages."""
        known = self._known.get(sid)
        if known is not None:
            return known
        path = self._file(sid)
        if not path.exists():
            self._known[sid] = 0
            return 0
        messages, _ = self._read_canonical(sid, repair=False)
        self._known[sid] = len(messages or [])
        return self._known[sid]

    def _append(self, sid: str, messages: list[dict]) -> None:
        with open(self._file(sid), "a", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m) + "\n")
        self._known[sid] = self._known.get(sid, 0) + len(messages)

    def _rewrite_jsonl(self, sid: str, messages: list[dict]) -> None:
        """Atomically replace the session's .jsonl with exactly `messages`. Both callers
        (the rare save() shrink and revert()) rewrite existing history, so a crash partway
        through must never truncate the file: write a temp file, close it, then swap in
        one step — the tmp-then-replace pattern of packages.jsonstate.save_json_state."""
        path = self._file(sid)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for m in messages:
                    f.write(json.dumps(m) + "\n")
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)  # never leave a scratch file behind
            raise

    def revert(self, sid: str, index: int) -> list[dict]:
        """Drop messages from `index` onward (the user message at `index` and everything
        after), keeping [0, index). Returns the dropped slice so the caller can prefill the
        composer with the original user text. The JSONL is append-only for normal turns;
        revert is the one explicit rewrite (a user action), under the store lock."""
        with self._lock:
            path = self._file(sid)
            if not path.exists():
                return []
            messages, _ = _parse_jsonl(path.read_text(encoding="utf-8"))
            if index <= 0 or index >= len(messages):
                return []
            dropped = messages[index:]
            self._rewrite_jsonl(sid, messages[:index])
            self._known[sid] = index
            self._conn.execute(
                "UPDATE sessions SET n_msgs = MAX(0, n_msgs - ?) WHERE session_id = ?",
                (len(dropped), sid),
            )
            self._conn.commit()
            return dropped

    def _backfill_counts(self) -> None:
        """One-time per session: move any inline blob into a .jsonl and persist
        title + n_msgs in the index. Skips already-migrated rows on later startups."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, messages, n_msgs, title FROM sessions"
            ).fetchall()
            for row in rows:
                sid = row["session_id"]
                jsonl = self._file(sid)
                if jsonl.exists() and row["title"] and row["n_msgs"]:
                    continue  # already migrated
                if jsonl.exists():
                    messages = self._read_jsonl(sid) or []
                elif row["messages"]:
                    try:
                        messages = json.loads(row["messages"])
                    except json.JSONDecodeError:
                        messages = []
                    if messages:
                        self._append(sid, messages)
                    self._conn.execute(
                        "UPDATE sessions SET messages = NULL WHERE session_id = ?",
                        (sid,),
                    )
                else:
                    messages = []
                self._conn.execute(
                    "UPDATE sessions SET n_msgs = ?, title = ? WHERE session_id = ?",
                    (len(messages), row["title"] or title_from(messages), sid),
                )
            self._conn.commit()

    # -- API --------------------------------------------------------------------
    def save(self, record: SessionRecord) -> None:
        sid = record.session_id
        with self._lock:
            # lazily migrate a legacy inline blob into the .jsonl
            if not self._file(sid).exists():
                row = self._conn.execute(
                    "SELECT messages FROM sessions WHERE session_id = ?", (sid,)
                ).fetchone()
                if row and row["messages"]:
                    try:
                        legacy = json.loads(row["messages"])
                    except json.JSONDecodeError:
                        legacy = []
                    if legacy:
                        # Same canonicalization as load(): never append raw mis-paired
                        # history — the offset below assumes the file is canonical.
                        legacy = self._repair_tool_pairing(legacy)
                        self._append(sid, legacy)

            existing = self._count(sid)
            if len(record.messages) > existing:
                self._append(sid, record.messages[existing:])
            elif len(record.messages) < existing:  # rare; not append-only
                self._rewrite_jsonl(sid, record.messages)
                self._known[sid] = len(record.messages)

            title = record.title or title_from(record.messages)
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, workspace, model, mode, title, agent, n_msgs, messages, extra_roots, grants, compaction, reasoning_effort, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace = excluded.workspace, model = excluded.model, mode = excluded.mode,
                    title = COALESCE(sessions.title, excluded.title), agent = excluded.agent,
                    n_msgs = excluded.n_msgs, messages = NULL, extra_roots = excluded.extra_roots,
                    grants = excluded.grants, compaction = excluded.compaction,
                    reasoning_effort = excluded.reasoning_effort,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    sid,
                    record.workspace,
                    record.model,
                    record.mode,
                    title,
                    record.agent,
                    len(record.messages),
                    json.dumps(record.extra_roots or []),
                    json.dumps(record.grants or {}),
                    json.dumps(record.compaction or {}),
                    record.reasoning_effort,
                ),
            )
            self._conn.commit()
        self.touch_workspace(record.workspace)

    def load(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            messages, drifted = self._read_canonical(session_id)
            if messages is None:
                # No .jsonl yet — promote the legacy inline blob to a canonical file now,
                # already repaired. Letting save()'s lazy migration copy the raw blob
                # would re-introduce the append-offset bug for mis-paired legacy history.
                try:
                    messages = json.loads(row["messages"] or "[]")
                except json.JSONDecodeError:
                    messages = []
                messages = self._repair_tool_pairing(messages)
                if messages:
                    self._rewrite_jsonl(session_id, messages)
                    self._known[session_id] = len(messages)
            elif drifted:
                # Canonicalize: the repaired history (reordered tool results, synthesised
                # placeholders, dropped corrupt lines) only exists in memory right now —
                # persist it and keep the index count in lockstep, or the next save()
                # would offset its append against the pre-repair disk and corrupt the
                # tail of the thread. Canonical message count is the only count that
                # counts; physical lines mean nothing.
                self._rewrite_jsonl(session_id, messages)
                self._known[session_id] = len(messages)
                self._conn.execute(
                    "UPDATE sessions SET n_msgs = ? WHERE session_id = ?",
                    (len(messages), session_id),
                )
                self._conn.commit()
        return SessionRecord(
            session_id=session_id,
            workspace=row["workspace"],
            model=row["model"],
            mode=row["mode"],
            messages=messages,
            title=_display_title(row),
            agent=row["agent"] or "code",
            message_count=len(messages),
            updated_at=row["updated_at"],
            extra_roots=_load_roots(
                row["extra_roots"] if "extra_roots" in row.keys() else None
            ),
            grants=_load_grants(row["grants"] if "grants" in row.keys() else None),
            # Auto-compaction state (OPE-27) — same defensive parse as grants.
            compaction=_load_grants(
                row["compaction"] if "compaction" in row.keys() else None
            ),
            pinned=bool(row["pinned"]),
            archived=bool(row["archived"]),
            origin=row["origin"],
            origin_label=row["origin_label"],
            reasoning_effort=(
                row["reasoning_effort"] if "reasoning_effort" in row.keys() else "auto"
            )
            or "auto",
        )

    def set_extra_roots(self, session_id: str, extra_roots: list[dict]) -> None:
        """Persist just the session's added folders, independent of its message log — used when
        the user adds/removes a folder (which may happen with no active engine)."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET extra_roots = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (json.dumps(extra_roots or []), session_id),
            )
            self._conn.commit()

    def list(self, *, workspace: str | None = None) -> list[SessionRecord]:
        with self._lock:
            if workspace is None:
                rows = self._conn.execute(
                    "SELECT * FROM sessions ORDER BY pinned DESC, updated_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM sessions WHERE workspace = ? ORDER BY pinned DESC, updated_at DESC",
                    (workspace,),
                ).fetchall()
        return [
            SessionRecord(
                session_id=r["session_id"],
                workspace=r["workspace"],
                model=r["model"],
                mode=r["mode"],
                messages=[],
                title=_display_title(r),
                agent=r["agent"] or "code",
                message_count=r["n_msgs"] or 0,
                updated_at=r["updated_at"],
                pinned=bool(r["pinned"]),
                archived=bool(r["archived"]),
                origin=r["origin"],
                origin_label=r["origin_label"],
                reasoning_effort=(
                    r["reasoning_effort"]
                    if "reasoning_effort" in r.keys()
                    else "auto"
                )
                or "auto",
            )
            for r in rows
        ]

    def touch_workspace(self, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO workspaces (path, last_used) VALUES (?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
                (path,),
            )
            self._conn.commit()

    def recent_workspaces(self, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM workspaces ORDER BY last_used DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r["path"] for r in rows]

    def canonicalize_workspaces(self) -> None:
        with self._lock:
            for (ws,) in self._conn.execute(
                "SELECT DISTINCT workspace FROM sessions WHERE workspace IS NOT NULL"
            ).fetchall():
                real = os.path.realpath(ws)
                if real != ws:
                    self._conn.execute(
                        "UPDATE sessions SET workspace = ? WHERE workspace = ?",
                        (real, ws),
                    )
            latest: dict[str, str] = {}
            for path, last in self._conn.execute(
                "SELECT path, last_used FROM workspaces"
            ).fetchall():
                real = os.path.realpath(path)
                if real not in latest or (last or "") > latest[real]:
                    latest[real] = last
            self._conn.execute("DELETE FROM workspaces")
            for path, last in latest.items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO workspaces (path, last_used) VALUES (?, ?)",
                    (path, last),
                )
            self._conn.commit()

    def delete(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
        path = self._file(session_id)
        if path.exists():
            path.unlink()
        return cur.rowcount > 0

    def rename(self, session_id: str, title: str) -> bool:
        clean = " ".join((title or "").split())[:120]
        if not clean:
            return False
        with self._lock:
            # renamed=1 makes the manual title final: auto-titling skips the session and
            # `_display_title` ignores any auto_title already there.
            cur = self._conn.execute(
                "UPDATE sessions SET title = ?, renamed = 1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (clean, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def set_auto_title(self, session_id: str, title: str) -> bool:
        """Store a generated title. Its own column — never `title` — so a manual rename
        (past or future) always wins; doesn't touch updated_at (a title landing after the
        turn must not reorder the session list)."""
        clean = " ".join((title or "").split())[:60]
        if not clean:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET auto_title = ? WHERE session_id = ? AND renamed = 0",
                (clean, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def title_state(self, session_id: str) -> dict | None:
        """The auto-title guard inputs: whether the user renamed and whether a generated
        title already exists. None when the session has no row yet."""
        with self._lock:
            row = self._conn.execute(
                "SELECT renamed, auto_title FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"renamed": bool(row["renamed"]), "auto_title": row["auto_title"]}

    def set_flags(
        self,
        session_id: str,
        *,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> bool:
        """Update pin/archive flags without touching updated_at (so pinning doesn't reorder)."""
        sets, params = [], []
        if pinned is not None:
            sets.append("pinned = ?")
            params.append(1 if pinned else 0)
        if archived is not None:
            sets.append("archived = ?")
            params.append(1 if archived else 0)
        if not sets:
            return False
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?",
                (*params, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def set_origin(self, session_id: str, origin: str, origin_label: str = "") -> bool:
        """Mark where a spawned session came from (§31). Set once at spawn; `save()` never
        names these columns, so per-turn saves can't clobber them (the pinned mechanism).
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET origin = ?, origin_label = ? WHERE session_id = ?",
                (origin, origin_label or None, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()
