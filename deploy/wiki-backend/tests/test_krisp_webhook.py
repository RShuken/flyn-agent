"""Tests for the Krisp webhook receiver."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmpwiki = tempfile.NamedTemporaryFile(suffix="-ol.db", delete=False)
_tmpwiki.close()
_tmpmeet = tempfile.NamedTemporaryFile(suffix="-meet.db", delete=False)
_tmpmeet.close()
os.environ["OL_WIKI_DB"] = _tmpwiki.name
os.environ["FLYN_MEETINGS_DB"] = _tmpmeet.name
os.environ["OL_WIKI_API_KEY"] = "test-key"
os.environ["FLYN_KRISP_TOKEN"] = "krisp-test-token"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from db import init_db as init_ol_db  # noqa: E402
from meetings_db import init_db as init_meet_db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    import meetings_db as mdb

    # Start with a clean meetings DB for this test module.
    Path(_tmpmeet.name).unlink(missing_ok=True)
    mdb._initialized = False
    mdb.DB_PATH = Path(_tmpmeet.name)
    # Re-pin the env var here (another test module's collection may have
    # overwritten it between our module-level set above and fixture execution).
    os.environ["FLYN_MEETINGS_DB"] = _tmpmeet.name

    init_ol_db()
    init_meet_db()
    with TestClient(app) as c:
        yield c


def test_missing_token_returns_401(client):
    r = client.post("/api/meetings/krisp", json={"event_id": "x"})
    assert r.status_code == 401


def test_wrong_token_returns_401(client):
    r = client.post(
        "/api/meetings/krisp",
        json={"event_id": "x"},
        headers={"X-OL-Krisp-Token": "wrong"},
    )
    assert r.status_code == 401


def test_valid_token_returns_200(client):
    r = client.post(
        "/api/meetings/krisp",
        json={"event_id": "ev-001"},
        headers={"X-OL-Krisp-Token": "krisp-test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["event_id"] == "ev-001"
    assert body["duplicate"] is False


def test_duplicate_event_id_returns_duplicate_true(client):
    """The core idempotency guarantee: re-POSTing the same event_id
    returns 200 with duplicate=true and does NOT raise."""
    payload = {"event_id": "ev-idem-once"}
    headers = {"X-OL-Krisp-Token": "krisp-test-token"}
    r1 = client.post("/api/meetings/krisp", json=payload, headers=headers)
    r2 = client.post("/api/meetings/krisp", json=payload, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True


def test_falsy_event_id_uses_hash_fallback_correctly(client):
    """Confirms _event_id_from's tightening: an explicit empty-string
    event_id falls through to the hash, NOT taken as the literal ID."""
    headers = {"X-OL-Krisp-Token": "krisp-test-token"}
    r = client.post("/api/meetings/krisp", json={"event_id": ""}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    # The hash fallback is 32 hex chars; empty-string event_id should not be used.
    assert body["event_id"] != ""
    assert len(body["event_id"]) == 32


def test_payload_extracts_meeting_and_upserts(client):
    payload = {
        "event_id": "ev-merge-1",
        "event_type": "transcript.created",
        "meeting": {
            "id": "mtg-42",
            "title": "Sprint sync",
            "url": "https://krisp.ai/m/mtg-42",
            "started_at": "2026-05-14T15:00:00Z",
            "ended_at": "2026-05-14T15:28:00Z",
            "duration_seconds": 1680,
            "attendees": [
                {"name": "Beth", "email": "beth@example.com"},
                {"name": "Ryan", "email": "ryanshuken@gmail.com"},
            ],
        },
        "transcript": {"text": "hello world"},
    }
    r = client.post(
        "/api/meetings/krisp", json=payload,
        headers={"X-OL-Krisp-Token": "krisp-test-token"},
    )
    assert r.status_code == 200

    import sqlite3
    conn = sqlite3.connect(os.environ["FLYN_MEETINGS_DB"])
    row = conn.execute(
        "SELECT title, transcript_text, attendees, status "
        "FROM meetings WHERE meeting_id = ?",
        ("mtg-42",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "Sprint sync"
    assert row[1] == "hello world"
    assert "beth@example.com" in row[2]
    assert row[3] == "pending"


def test_second_event_merges_into_same_meeting(client):
    # First event: transcript
    p1 = {
        "event_id": "ev-mrg-a",
        "event_type": "transcript.created",
        "meeting": {"id": "mtg-merge", "title": "T"},
        "transcript": {"text": "T-text"},
    }
    client.post("/api/meetings/krisp", json=p1,
                headers={"X-OL-Krisp-Token": "krisp-test-token"})
    # Second event: notes for same meeting
    p2 = {
        "event_id": "ev-mrg-b",
        "event_type": "notes.generated",
        "meeting": {"id": "mtg-merge", "title": "T"},
        "notes": {"text": "N-text"},
    }
    client.post("/api/meetings/krisp", json=p2,
                headers={"X-OL-Krisp-Token": "krisp-test-token"})

    import sqlite3
    conn = sqlite3.connect(os.environ["FLYN_MEETINGS_DB"])
    row = conn.execute(
        "SELECT transcript_text, notes_text FROM meetings WHERE meeting_id = ?",
        ("mtg-merge",),
    ).fetchone()
    conn.close()
    assert row[0] == "T-text"
    assert row[1] == "N-text"
