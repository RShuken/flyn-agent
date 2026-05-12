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


# -------------------- Phase 2: Webhooks --------------------

def test_webhooks_crud(client):
    """Rubric 2.2 + 2.3 + 2.4 — create / list / delete."""
    AUTH = {"X-API-Key": "test-key-not-secret"}
    # Initially empty
    r = client.get("/api/webhooks", headers=AUTH)
    assert r.status_code == 200
    initial_count = len(r.json())

    # Create
    r = client.post(
        "/api/webhooks",
        json={"target_url": "https://example.invalid/hook",
              "event_types": ["decision.created"],
              "label": "test"},
        headers=AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["target_url"] == "https://example.invalid/hook"
    assert body["event_types"] == ["decision.created"]
    new_id = body["id"]

    # List sees it
    r = client.get("/api/webhooks", headers=AUTH)
    listed = [w for w in r.json() if w["id"] == new_id]
    assert len(listed) == 1

    # Delete
    r = client.delete(f"/api/webhooks/{new_id}", headers=AUTH)
    assert r.status_code == 204
    r = client.get(f"/api/webhooks/{new_id}", headers=AUTH)
    # GET single not implemented; verify via list
    r = client.get("/api/webhooks", headers=AUTH)
    assert not any(w["id"] == new_id for w in r.json())


def test_webhook_requires_auth(client):
    """Rubric 1.6 extension — webhooks endpoints reject anon."""
    r = client.get("/api/webhooks")
    assert r.status_code == 401
    r = client.post("/api/webhooks", json={"target_url": "https://x.invalid/h"})
    assert r.status_code == 401


def test_webhook_fires_on_decision(client, tmp_path, monkeypatch):
    """Rubric 2.6 — decision.created fires the webhook (with HMAC sig).

    We mock urllib.request.urlopen via webhooks._post to capture instead of
    actually POSTing.
    """
    import webhooks as wh
    captured: list[dict] = []

    def fake_post(url, body, headers, timeout=5.0):
        captured.append({"url": url, "body": body, "headers": headers})
        return 200

    monkeypatch.setattr(wh, "_post", fake_post)

    AUTH = {"X-API-Key": "test-key-not-secret"}
    # Subscribe
    r = client.post(
        "/api/webhooks",
        json={"target_url": "https://capture.invalid/hook",
              "event_types": ["decision.created"],
              "secret": "shh"},
        headers=AUTH,
    )
    assert r.status_code == 201

    # Trigger
    r = client.post(
        "/api/decisions",
        json={"decided_by": "Test",
              "summary": "webhook smoke",
              "body_md": "fires the hook",
              "question_ids": ["A.1"]},
        headers=AUTH,
    )
    assert r.status_code == 201

    # Webhook fires via daemon thread — give it a moment
    import time
    time.sleep(0.3)
    assert len(captured) >= 1, "webhook was not fired"
    delivery = captured[-1]
    assert delivery["url"] == "https://capture.invalid/hook"
    assert "X-OL-Webhook-Signature" in delivery["headers"]
    # Body should contain the event name
    import json as _j
    payload = _j.loads(delivery["body"].decode())
    assert payload["event"] == "decision.created"
    assert payload["data"]["summary"] == "webhook smoke"

    # HMAC sig verification
    import hmac, hashlib
    expected = hmac.new(b"shh", delivery["body"], hashlib.sha256).hexdigest()
    assert delivery["headers"]["X-OL-Webhook-Signature"] == expected


def test_webhook_failure_is_best_effort(client, monkeypatch):
    """Rubric 2.8 — receiver 500 doesn't break the mutation."""
    import webhooks as wh

    def fake_post(url, body, headers, timeout=5.0):
        return 500  # simulate receiver error

    monkeypatch.setattr(wh, "_post", fake_post)

    AUTH = {"X-API-Key": "test-key-not-secret"}
    # subscribe
    client.post("/api/webhooks",
                json={"target_url": "https://broken.invalid/hook",
                      "event_types": ["*"]},
                headers=AUTH)

    # Decision still succeeds even though webhook fails
    r = client.post(
        "/api/decisions",
        json={"decided_by": "Test",
              "summary": "broken hook test",
              "body_md": "doesn't matter",
              "question_ids": []},
        headers=AUTH,
    )
    assert r.status_code == 201, "mutation should succeed despite webhook failure"
