from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("FLYN_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("FLYN_GRAPHITI_URL", "http://localhost:8100")
    from flyn_memory_router.server import build_app  # import after env set
    app = build_app(http_client=_FakeHttpOK())
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws" / "MEMORY.md").write_text("# MEMORY\n\n## Active pins\n\n")
    return TestClient(app)


class _FakeHttpOK:
    def post(self, url, *, json):
        class R:
            status_code = 200
            text = ""
            def json(self_inner):
                return {"uuid": "fake"}
        return R()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_ingest_warm_roundtrip(client):
    payload = {
        "source": "orchestrator", "event_type": "task_completed",
        "subject": "T-0042", "body": "T-0042 completed, PR #48 merged",
        "dedup_key": "orch-T-0042-completed",
    }
    r = client.post("/api/memory/ingest", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["importance"] == "warm"
    assert "warm" in body["tiers_written"]


def test_ingest_dedup_second_call(client):
    payload = {
        "source": "orchestrator", "event_type": "task_completed",
        "subject": "T-1", "body": "x" * 20, "dedup_key": "orch-T-1",
    }
    client.post("/api/memory/ingest", json=payload)
    r2 = client.post("/api/memory/ingest", json=payload)
    assert r2.json()["deduped"] is True


def test_pin_owner_only(client):
    r = client.post("/api/memory/pin",
                    json={"subject": "P-1", "body": "pin me",
                          "sender_role": "teammate"})
    assert r.status_code == 403
    r2 = client.post("/api/memory/pin",
                     json={"subject": "P-1", "body": "pin me",
                           "sender_role": "owner"})
    assert r2.status_code == 200


def test_unpin_owner_only(client):
    client.post("/api/memory/pin",
                json={"subject": "P-2", "body": "x" * 20, "sender_role": "owner"})
    r = client.delete("/api/memory/pin/P-2?sender_role=teammate")
    assert r.status_code == 403
    r2 = client.delete("/api/memory/pin/P-2?sender_role=owner")
    assert r2.status_code == 200


def test_decay_owner_only(client):
    r = client.post("/api/memory/maintenance/decay",
                    json={"sender_role": "teammate"})
    assert r.status_code == 403
    r2 = client.post("/api/memory/maintenance/decay",
                     json={"sender_role": "owner"})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
