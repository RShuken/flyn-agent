"""Tests for the Flyn-wide meeting inbox SQLite layer."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import meetings_db as mdb  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    """Reset the meetings_db module state + DB file before each test
    so tests don't depend on order."""
    # Also reset DB_PATH in case another test module set FLYN_MEETINGS_DB
    # to a different temp file before this module was imported.
    mdb.DB_PATH = Path(_tmpdb.name)
    Path(_tmpdb.name).unlink(missing_ok=True)
    mdb._initialized = False
    yield


def test_init_creates_tables():
    mdb.init_db()
    conn = mdb._connect()
    try:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"meeting_events", "meetings", "meeting_audit"}.issubset(names)
    finally:
        conn.close()


def test_init_is_idempotent():
    mdb.init_db()
    mdb.init_db()  # second call must not raise
    conn = mdb._connect()
    try:
        # Insert a row, confirm second init didn't wipe data.
        conn.execute(
            "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
            ("ev-1", "{}"),
        )
        mdb.init_db()
        n = conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_event_id_unique_constraint():
    mdb.init_db()
    conn = mdb._connect()
    try:
        conn.execute(
            "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
            ("ev-dup", "{}"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
                ("ev-dup", "{}"),
            )
    finally:
        conn.close()
