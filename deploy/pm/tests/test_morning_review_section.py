"""Test the new review-meetings section appended to the morning digest."""

from __future__ import annotations

import json
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "wiki-backend"))

import meetings_db  # noqa: E402
import morning_standup as ms  # noqa: E402


@pytest.fixture
def seeded_db():
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.DB_PATH = Path(_tmpdb.name)
    meetings_db.init_db()
    conn = meetings_db._connect()
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, started_at, attendees, "
        "status, classifier_reason) VALUES "
        "(?, ?, ?, ?, 'review', ?)",
        ("m1", "Sync w/ Jen", "2026-05-14T15:00:00Z",
         json.dumps([{"email": "jen@example.com"}]),
         "no rule matched, llm-low"),
    )
    conn.close()
    return _tmpdb.name


def test_review_section_lists_meetings(seeded_db, tmp_path):
    state_file = tmp_path / "last-review-list.json"
    section = ms.build_review_meetings_section(state_path=state_file)
    assert "1." in section
    assert "Sync w/ Jen" in section
    assert "/route 1 " in section  # at least one /route hint present
    assert state_file.exists()
    saved = json.loads(state_file.read_text())
    assert saved[0]["meeting_id"] == "m1"


def test_review_section_empty_returns_empty_string(tmp_path):
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.DB_PATH = Path(_tmpdb.name)
    meetings_db.init_db()
    state_file = tmp_path / "last-review-list.json"
    section = ms.build_review_meetings_section(state_path=state_file)
    assert section == ""
