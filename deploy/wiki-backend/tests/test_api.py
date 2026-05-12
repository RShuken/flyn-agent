"""Smoke tests for the OL wiki backend.

Uses FastAPI TestClient with an in-memory SQLite DB (via OL_WIKI_DB override).
Seeds a tiny synthetic dataset rather than the real registry so tests are
hermetic + fast.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Use a temp DB file (in-memory doesn't survive across connections in our pattern)
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["OL_WIKI_DB"] = _tmpdb.name
os.environ["OL_WIKI_API_KEY"] = "test-key-not-secret"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402
from db import init_db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_db()
    # Seed a minimal dataset directly
    import sqlite3
    conn = sqlite3.connect(_tmpdb.name)
    conn.execute(
        "INSERT INTO questions (id, section, section_title, text, bucket, owner, depends_on, target_sprint) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("A.1", "A", "Initial Phonics", "Sentence presentation flow",
         "ai-does", "Rebecca Patterson", "[]", 1),
    )
    conn.execute(
        "INSERT INTO questions (id, section, section_title, text, bucket, owner, depends_on, target_sprint) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("N.1", "N", "Conflicts", "3 vs 4 score buckets",
         "Conflict", "Sarah Scott Frank", "[]", 1),
    )
    conn.commit()
    conn.close()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["questions_count"] == 2


def test_list_questions(client):
    r = client.get("/api/questions")
    assert r.status_code == 200
    qs = r.json()
    assert len(qs) == 2
    ids = {q["id"] for q in qs}
    assert ids == {"A.1", "N.1"}


def test_filter_by_owner(client):
    r = client.get("/api/questions?owner=Sarah Scott Frank")
    assert r.status_code == 200
    qs = r.json()
    assert len(qs) == 1 and qs[0]["id"] == "N.1"


def test_get_single_question(client):
    r = client.get("/api/questions/A.1")
    assert r.status_code == 200
    assert r.json()["id"] == "A.1"
    r404 = client.get("/api/questions/X.999")
    assert r404.status_code == 404


def test_write_requires_auth(client):
    # No header → 401
    r = client.post("/api/questions/A.1/answer", json={"answer_text": "yes", "answered_by": "x"})
    assert r.status_code == 401

    # Wrong header → 401
    r = client.post("/api/questions/A.1/answer", json={"answer_text": "yes", "answered_by": "x"},
                    headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_answer_question(client):
    r = client.post(
        "/api/questions/A.1/answer",
        json={"answer_text": "Sentences are pre-authored per skill.", "answered_by": "Rebecca Patterson"},
        headers={"X-API-Key": "test-key-not-secret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "answered"
    assert body["answered_by"] == "Rebecca Patterson"
    assert body["answer_text"] == "Sentences are pre-authored per skill."

    # Verify audit log
    r_audit = client.get("/api/audit", headers={"X-API-Key": "test-key-not-secret"})
    assert r_audit.status_code == 200
    entries = r_audit.json()
    actions = [e["action"] for e in entries]
    assert "question.answered" in actions


def test_create_decision(client):
    r = client.post(
        "/api/decisions",
        json={
            "decided_by": "Sarah Scott Frank",
            "summary": "4 score buckets confirmed",
            "body_md": "Green / Yellow-warmup / Yellow-stay / Red.",
            "question_ids": ["N.1"],
        },
        headers={"X-API-Key": "test-key-not-secret"},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["summary"] == "4 score buckets confirmed"
    assert "N.1" in d["question_ids"]

    r_list = client.get("/api/decisions")
    assert r_list.status_code == 200
    assert any(x["summary"] == "4 score buckets confirmed" for x in r_list.json())


def test_stats(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    s = r.json()
    assert s["questions_total"] == 2
    assert s["by_status"]["answered"] == 1
    assert s["decisions_total"] == 1
