"""Per-owner SQLite schema for conversation messages.

Each owner has their own DB file (`<owner_id>.db` under conv_root). Schema
is idempotent (CREATE TABLE IF NOT EXISTS). WAL mode for concurrent reads
during writes. FTS5 virtual table indexes the redacted body + summary —
NOT the encrypted_raw BLOB.

Triggers keep the FTS5 index in sync with the messages table on insert,
update, and delete. Summary updates flow through the AFTER UPDATE trigger
which deletes the old FTS row and re-inserts the new one.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    sender_id     TEXT NOT NULL,
    thread_id     TEXT,
    reply_to_id   INTEGER,
    ts            TEXT NOT NULL,
    body          TEXT NOT NULL,
    attachments   TEXT,
    summary       TEXT,
    encrypted_raw BLOB NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    body, summary, content=messages, content_rowid=id
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_ts ON messages(thread_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_sender_ts ON messages(sender_id, ts DESC);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, body, summary)
      VALUES (new.id, new.body, COALESCE(new.summary, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, summary)
      VALUES('delete', old.id, old.body, COALESCE(old.summary, ''));
    INSERT INTO messages_fts(rowid, body, summary)
      VALUES (new.id, new.body, COALESCE(new.summary, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, body, summary)
      VALUES('delete', old.id, old.body, COALESCE(old.summary, ''));
END;
"""


# FTS5 reserved tokens at the start/end of a query, or as bare standalone
# tokens, cause syntax errors. Strip them rather than let users hit a 500.
_FTS5_RESERVED = {"AND", "OR", "NOT", "NEAR"}

# Whitelist: keep alphanumerics, underscore, dot, hyphen (common in IDs/URLs)
# and basic Unicode word characters. Everything else (quotes, parens, *, :, etc.)
# becomes a space. The result is split + re-joined so empty terms vanish.
_FTS5_SAFE_TERM = re.compile(r"[^\w\.\-]+", re.UNICODE)


def _sanitize_fts5_query(q: str) -> str:
    """Reduce arbitrary input to a safe space-separated FTS5 term list.

    Reserved boolean operators are dropped because users rarely intend them
    (e.g. "what AND why" should be a phrase search, not boolean AND).
    Empty result → caller should return [].
    """
    cleaned = _FTS5_SAFE_TERM.sub(" ", q)
    terms = [t for t in cleaned.split() if t and t.upper() not in _FTS5_RESERVED]
    return " ".join(terms)


@dataclass(frozen=True)
class ConvMessage:
    channel: str
    sender_id: str
    thread_id: str | None
    reply_to_id: int | None
    ts: str
    body: str
    attachments: list[dict]
    encrypted_raw: bytes


@dataclass(frozen=True)
class StoredMessage:
    row_id: int
    channel: str
    sender_id: str
    thread_id: str | None
    reply_to_id: int | None
    ts: str
    body: str
    attachments: list[dict]
    summary: str | None
    encrypted_raw: bytes
    fts_score: float = 0.0


class ConvDb:
    def __init__(self, owner_id: str, path: Path) -> None:
        self.owner_id = owner_id
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Open a SQLite connection, yield it, commit on success, close on exit.

        sqlite3.Connection as a plain context manager only manages transactions;
        it does not close the connection. Without this wrapper each call leaked
        ~3 file descriptors (db + WAL + SHM) and the OS fd table would saturate
        under sustained load (verified at ~400 fds after 300 ops).
        """
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def write(self, msg: ConvMessage) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO messages "
                "(channel, sender_id, thread_id, reply_to_id, ts, body, "
                "attachments, summary, encrypted_raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (msg.channel, msg.sender_id, msg.thread_id, msg.reply_to_id,
                 msg.ts, msg.body, json.dumps(msg.attachments), msg.encrypted_raw),
            )
            return cur.lastrowid

    def update_summary(self, row_id: int, summary: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE messages SET summary = ? WHERE id = ?", (summary, row_id))

    def search(self, q: str, top_k: int = 30) -> list[StoredMessage]:
        if not q.strip():
            return []
        # Sanitize arbitrary user input into a safe FTS5 query. Bare quotes,
        # lone AND/OR, unbalanced parens, etc. cause sqlite3.OperationalError.
        # We strip FTS5-reserved syntax and reassemble as space-joined terms.
        safe_q = _sanitize_fts5_query(q)
        if not safe_q:
            return []
        # Defense in depth: even after sanitization, wrap MATCH in try/except
        # so any future FTS5 quirk degrades to "no results" instead of a 500.
        # FTS5 MATCH; rank is BM25-derived (negative; lower = better)
        try:
            with self._lock, self._conn() as c:
                rows = c.execute(
                    "SELECT m.id, m.channel, m.sender_id, m.thread_id, m.reply_to_id, "
                    "m.ts, m.body, m.attachments, m.summary, m.encrypted_raw, "
                    "messages_fts.rank AS rank "
                    "FROM messages_fts "
                    "JOIN messages m ON m.id = messages_fts.rowid "
                    "WHERE messages_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (safe_q, top_k),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("conv.search: FTS5 rejected query %r (sanitized=%r): %s",
                           q, safe_q, exc)
            return []
        return [self._row_to_msg(r, fts_score=-(r["rank"] or 0.0)) for r in rows]

    def get_by_thread(self, thread_id: str, limit: int = 50) -> list[StoredMessage]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE thread_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def get_by_id(self, row_id: int) -> StoredMessage | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM messages WHERE id = ?", (row_id,)).fetchone()
        return self._row_to_msg(row) if row else None

    def stats(self) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS oldest, MAX(ts) AS newest, "
                "SUM(CASE WHEN summary IS NULL THEN 1 ELSE 0 END) AS backlog "
                "FROM messages"
            ).fetchone()
        return {
            "owner": self.owner_id,
            "messages": row["n"],
            "oldest_ts": row["oldest"],
            "newest_ts": row["newest"],
            "summary_backlog": row["backlog"] or 0,
        }

    @staticmethod
    def _row_to_msg(row: sqlite3.Row, fts_score: float = 0.0) -> StoredMessage:
        return StoredMessage(
            row_id=row["id"],
            channel=row["channel"],
            sender_id=row["sender_id"],
            thread_id=row["thread_id"],
            reply_to_id=row["reply_to_id"],
            ts=row["ts"],
            body=row["body"],
            attachments=json.loads(row["attachments"]) if row["attachments"] else [],
            summary=row["summary"],
            encrypted_raw=row["encrypted_raw"],
            fts_score=fts_score,
        )
