"""Conv-Tier 2.0 schema + migration tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flyn_memory_router.conv2.schema import (
    SCHEMA_VERSION,
    current_version,
    migrate,
    open_db,
)


def test_migrate_creates_all_tables(tmp_path: Path):
    """Fresh DB: migrate creates workflow + queue + dead-letter + schema_version."""
    db = tmp_path / "owner.db"
    version = migrate(db)
    assert version == SCHEMA_VERSION

    with open_db(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "conversation_workflow" in tables
        assert "work_queue" in tables
        assert "dead_letter_queue" in tables
        assert "schema_version" in tables


def test_migrate_is_idempotent(tmp_path: Path):
    """Running migrate twice is a no-op (no errors, same version)."""
    db = tmp_path / "owner.db"
    migrate(db)
    migrate(db)
    assert current_version(db) == SCHEMA_VERSION


def test_workflow_indexes_created(tmp_path: Path):
    """The stuck-detection indexes exist and are usable."""
    db = tmp_path / "owner.db"
    migrate(db)
    with open_db(db) as conn:
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_workflow_state" in indexes
        assert "idx_workflow_trace" in indexes
        assert "idx_queue_stage_next" in indexes


def test_current_version_returns_none_for_missing_db(tmp_path: Path):
    """current_version on a non-existent DB returns None."""
    db = tmp_path / "not-there.db"
    assert current_version(db) is None


def test_migrate_backfills_existing_v1_messages(tmp_path: Path):
    """If a v1 messages table exists, migrate backfills workflow rows for each."""
    db = tmp_path / "owner.db"
    # Simulate v1 messages table with some pre-existing rows
    with open_db(db) as conn:
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                thread_id TEXT,
                reply_to_id INTEGER,
                ts TEXT NOT NULL,
                body TEXT NOT NULL,
                attachments TEXT,
                summary TEXT,
                encrypted_raw BLOB NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO messages(channel, sender_id, ts, body, encrypted_raw) "
            "VALUES (?, ?, ?, ?, ?)",
            ("telegram", "7191564227", "2026-05-19T22:54:27", "hello", b"\x00" * 32),
        )
        conn.execute(
            "INSERT INTO messages(channel, sender_id, ts, body, summary, encrypted_raw) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("telegram", "7191564227", "2026-05-19T22:55:00", "summarized one",
             "this was summarized", b"\x00" * 32),
        )

    # Now migrate — should backfill workflow rows
    migrate(db)

    with open_db(db) as conn:
        rows = conn.execute(
            "SELECT message_id, state, encrypted_at, summarized_at "
            "FROM conversation_workflow ORDER BY message_id"
        ).fetchall()
        assert len(rows) == 2
        # Row 1 had encrypted_raw but no summary → state=encrypted
        assert rows[0]["state"] == "encrypted"
        assert rows[0]["encrypted_at"] is not None
        assert rows[0]["summarized_at"] is None
        # Row 2 had both → state=summarized
        assert rows[1]["state"] == "summarized"
        assert rows[1]["summarized_at"] is not None


def test_migrate_does_not_double_backfill(tmp_path: Path):
    """Running migrate twice doesn't create duplicate workflow rows."""
    db = tmp_path / "owner.db"
    with open_db(db) as conn:
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL, sender_id TEXT NOT NULL,
                thread_id TEXT, reply_to_id INTEGER, ts TEXT NOT NULL,
                body TEXT NOT NULL, attachments TEXT, summary TEXT,
                encrypted_raw BLOB NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO messages(channel, sender_id, ts, body, encrypted_raw) "
            "VALUES (?, ?, ?, ?, ?)",
            ("tg", "x", "ts", "body", b"\x00" * 32),
        )
    migrate(db)
    migrate(db)
    with open_db(db) as conn:
        count = conn.execute(
            "SELECT count(*) FROM conversation_workflow"
        ).fetchone()[0]
        assert count == 1


def test_wal_mode_enabled(tmp_path: Path):
    """Schema migration sets WAL journal mode (concurrent readers)."""
    db = tmp_path / "owner.db"
    migrate(db)
    with open_db(db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
