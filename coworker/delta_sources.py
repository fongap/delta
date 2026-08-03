"""Local source registration and citation primitives for Delta."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


class SourceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY, notebook_id TEXT NOT NULL, path TEXT NOT NULL,
                content_hash TEXT NOT NULL, title TEXT NOT NULL,
                UNIQUE(notebook_id, path)
            )"""
        )
        self._conn.commit()

    def register(self, notebook_id: str, path: str | Path, *, title: str = "") -> dict:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        label = title.strip() or source.name
        self._conn.execute(
            """INSERT INTO sources(notebook_id,path,content_hash,title) VALUES(?,?,?,?)
               ON CONFLICT(notebook_id,path) DO UPDATE SET content_hash=excluded.content_hash,title=excluded.title""",
            (notebook_id, str(source), digest, label),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM sources WHERE notebook_id=? AND path=?", (notebook_id, str(source))).fetchone()
        return dict(row)

    def citation(self, notebook_id: str, path: str | Path, locator: str) -> dict:
        source = self._conn.execute(
            "SELECT * FROM sources WHERE notebook_id=? AND path=?", (notebook_id, str(Path(path).expanduser().resolve())),
        ).fetchone()
        if source is None:
            raise KeyError("source is not registered in this notebook")
        return {"source_id": source["id"], "title": source["title"], "path": source["path"], "locator": locator, "content_hash": source["content_hash"]}

    def list(self, notebook_id: str) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM sources WHERE notebook_id=? ORDER BY title", (notebook_id,)).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
