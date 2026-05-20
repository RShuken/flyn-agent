"""Conv-Tier 2.0 SQLite schema + migration.

Two NEW tables on top of v1's `messages` table:

- conversation_workflow: per-message lifecycle state (mutable). One row
  per message; tracks which stages completed when, retry attempts, last
  error, idempotency keys, trace_id.

- work_queue: durable typed work queue. Workers claim jobs via atomic
  UPDATE...RETURNING; crash recovery scans this table on startup and
  re-enqueues any in-flight-but-expired claims.

Both tables reference messages(id) but do NOT modify the messages
schema — v2 is additive so v1 keeps working until cutover.

All DDL is idempotent (CREATE TABLE IF NOT EXISTS). Migration is
re-runnable.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 1

# DDL — strictly additive, never DROP. All CREATEs are IF NOT EXISTS so
# the migration can re-run safely.
#
# Includes the v1 `messages` table definition so v2 can run standalone
# without depending on a prior v1 install. When v1 is already present,
# the IF NOT EXISTS clauses make this a no-op.
_DDL = """
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

CREATE TABLE IF NOT EXISTS conversation_workflow (
    message_id      INTEGER PRIMARY KEY,
    state           TEXT NOT NULL,
    attempts_encrypt   INTEGER NOT NULL DEFAULT 0,
    attempts_index     INTEGER NOT NULL DEFAULT 0,
    attempts_summarize INTEGER NOT NULL DEFAULT 0,
    attempts_promote   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    last_error_stage TEXT,
    idempotency_key_summarize TEXT,
    idempotency_key_promote   TEXT,
    trace_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    encrypted_at    TEXT,
    indexed_at      TEXT,
    summarized_at   TEXT,
    promoted_at     TEXT,
    completed_at    TEXT,
    failed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflow_state ON conversation_workflow(state, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_trace ON conversation_workflow(trace_id);
CREATE INDEX IF NOT EXISTS idx_workflow_stuck ON conversation_workflow(state, created_at)
    WHERE state NOT IN ('complete', 'failed');

CREATE TABLE IF NOT EXISTS work_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,
    message_id      INTEGER NOT NULL,
    trace_id        TEXT NOT NULL,
    enqueued_at     TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    in_flight_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_stage_next
    ON work_queue(stage, next_attempt_at, in_flight_until);
CREATE INDEX IF NOT EXISTS idx_queue_message ON work_queue(message_id);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,
    message_id      INTEGER NOT NULL,
    trace_id        TEXT NOT NULL,
    attempts        INTEGER NOT NULL,
    last_error      TEXT NOT NULL,
    failed_at       TEXT NOT NULL,
    original_payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_stage ON dead_letter_queue(stage, failed_at DESC);

CREATE TABLE IF NOT EXISTS schema_version (
    component       TEXT PRIMARY KEY,
    version         INTEGER NOT NULL,
    applied_at      TEXT NOT NULL
);
"""


@contextlib.contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection with WAL + row factory; commit on success, close on exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we manage TX explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def migrate(db_path: Path) -> int:
    """Apply v2 schema to an owner DB. Returns the resulting schema version.

    Safe to re-run. If `messages` table already exists (from v1), backfills
    a `conversation_workflow` row for every existing message that doesn't
    already have one — preserves existing v1 rows during shadow-mode rollout.
    """
    with open_db(db_path) as conn:
        # 1. Apply DDL
        conn.executescript(_DDL)

        # 2. Record schema version
        conn.execute(
            "INSERT OR REPLACE INTO schema_version(component, version, applied_at) "
            "VALUES ('conv-tier-2.0', ?, datetime('now'))",
            (SCHEMA_VERSION,),
        )

        # 3. Backfill workflow rows for any v1 messages that don't have one yet.
        #    Existing v1 rows have body + (sometimes) summary already; we mark
        #    their stage based on what's been done:
        #      - encrypted_raw is non-null  → encrypted stage done
        #      - summary is non-null         → summarized stage done
        #    indexed_at and promoted_at stay NULL until v2 actively processes them.
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if cur.fetchone() is not None:
            conn.execute("""
                INSERT INTO conversation_workflow (
                    message_id, state, trace_id, created_at,
                    encrypted_at, summarized_at
                )
                SELECT
                    m.id,
                    CASE
                        WHEN m.summary IS NOT NULL THEN 'summarized'
                        WHEN m.encrypted_raw IS NOT NULL THEN 'encrypted'
                        ELSE 'received'
                    END as state,
                    'migrated-' || m.id as trace_id,
                    m.ts as created_at,
                    CASE WHEN m.encrypted_raw IS NOT NULL THEN m.ts ELSE NULL END as encrypted_at,
                    CASE WHEN m.summary IS NOT NULL THEN m.ts ELSE NULL END as summarized_at
                FROM messages m
                LEFT JOIN conversation_workflow w ON w.message_id = m.id
                WHERE w.message_id IS NULL
            """)

        return SCHEMA_VERSION


def current_version(db_path: Path) -> int | None:
    """Return the v2 schema version applied to this DB, or None if not yet."""
    if not db_path.exists():
        return None
    with open_db(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT version FROM schema_version WHERE component = 'conv-tier-2.0'"
            ).fetchone()
            return row["version"] if row else None
        except sqlite3.OperationalError:
            return None
