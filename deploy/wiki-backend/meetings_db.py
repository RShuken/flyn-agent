"""SQLite layer for the Flyn-wide meeting inbox.

Lives next to db.py (OL wiki) but uses a separate file (~/.openclaw/data/
flyn-meetings.db) so meeting data is logically partitioned from OL-specific
state. Same patterns: idempotent CREATE TABLE IF NOT EXISTS, WAL mode,
one connection per request via FastAPI dependency.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

DB_PATH = Path(os.environ.get(
    "FLYN_MEETINGS_DB",
    str(Path.home() / ".openclaw" / "data" / "flyn-meetings.db"),
))

_init_lock = threading.Lock()
_initialized = False


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS meeting_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT    NOT NULL UNIQUE,
        received_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        source       TEXT    NOT NULL DEFAULT 'krisp',
        event_type   TEXT,
        meeting_id   TEXT,
        raw_payload  TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meetings (
        meeting_id        TEXT PRIMARY KEY,
        title             TEXT,
        started_at        TEXT,
        ended_at          TEXT,
        duration_seconds  INTEGER,
        meeting_url       TEXT,
        attendees         TEXT    NOT NULL DEFAULT '[]',
        transcript_text   TEXT,
        notes_text        TEXT,
        outline_text      TEXT,
        key_points_text   TEXT,
        status            TEXT    NOT NULL DEFAULT 'pending',
        routed_project    TEXT,
        routed_commit_sha TEXT,
        classifier_reason TEXT,
        classifier_confidence TEXT,
        first_seen_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        routed_at         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meeting_audit (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT    NOT NULL DEFAULT (datetime('now')),
        meeting_id TEXT,
        actor      TEXT    NOT NULL,
        action     TEXT    NOT NULL,
        payload    TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_meeting ON meeting_events(meeting_id)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_started ON meetings(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_meeting ON meeting_audit(meeting_id)",
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = _connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            for stmt in SCHEMA:
                conn.execute(stmt)
        finally:
            conn.close()
        _initialized = True


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str,
          meeting_id: str | None = None, payload: str = "{}") -> None:
    conn.execute(
        "INSERT INTO meeting_audit (meeting_id, actor, action, payload) "
        "VALUES (?, ?, ?, ?)",
        (meeting_id, actor, action, payload),
    )
