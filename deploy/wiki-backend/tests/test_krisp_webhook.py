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
