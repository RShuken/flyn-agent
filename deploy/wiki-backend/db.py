"""SQLite layer for the OL project-management wiki backend.

Single-file schema migration: idempotent CREATE TABLE IF NOT EXISTS + ALTER
TABLE ADD COLUMN guarded by PRAGMA introspection. Connection pattern: one
connection per request (FastAPI dependency), WAL mode set once at startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(os.environ.get(
    "OL_WIKI_DB",
    str(Path.home() / ".openclaw" / "data" / "ol-pm.db"),
))

_init_lock = threading.Lock()
_initialized = False


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS questions (
        id            TEXT    PRIMARY KEY,
        section       TEXT    NOT NULL,
        section_title TEXT    NOT NULL,
        text          TEXT    NOT NULL,
        ask           TEXT,
        bucket        TEXT    NOT NULL,
        source        TEXT,
        owner         TEXT    NOT NULL,
        status        TEXT    NOT NULL DEFAULT 'open',
        depends_on    TEXT    NOT NULL DEFAULT '[]',   -- JSON array of ids
        target_sprint INTEGER,
        answered_at   TEXT,
        answered_by   TEXT,
        answer_text   TEXT,
        source_doc    TEXT,                            -- e.g. registry md path
        updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        decided_at    TEXT    NOT NULL DEFAULT (datetime('now')),
        decided_by    TEXT    NOT NULL,
        summary       TEXT    NOT NULL,
        body_md       TEXT    NOT NULL,
        question_ids  TEXT    NOT NULL DEFAULT '[]',   -- JSON array of ids
        source_meeting TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ts      TEXT    NOT NULL DEFAULT (datetime('now')),
        actor   TEXT    NOT NULL,
        action  TEXT    NOT NULL,
        payload TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_questions_owner ON questions(owner)",
    "CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status)",
    "CREATE INDEX IF NOT EXISTS idx_questions_section ON questions(section)",
    "CREATE INDEX IF NOT EXISTS idx_questions_sprint ON questions(target_sprint)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)",
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Idempotent migrations + WAL mode setup. Safe to call repeatedly."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            for stmt in SCHEMA:
                conn.execute(stmt)
        _initialized = True


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency. Yields a fresh connection per request."""
    init_db()
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str, payload: dict[str, Any]) -> None:
    """Append a row to the audit_log table."""
    conn.execute(
        "INSERT INTO audit_log (actor, action, payload) VALUES (?, ?, ?)",
        (actor, action, json.dumps(payload, sort_keys=True)),
    )
