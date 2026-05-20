"""POST /api/memory/ingest with conversation_message → conv.db roundtrip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Standard test env: tmp dirs + stubbed Keychain + seeded principals."""
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("FLYN_CONV_ROOT", str(tmp_path / "conv"))
    # Graphiti unreachable URL — adapter fire-and-forget swallows errors
    monkeypatch.setenv("FLYN_GRAPHITI_URL", "http://localhost:9999")

    # Seed principals.json
    (tmp_path / "conv").mkdir(parents=True, exist_ok=True)
    (tmp_path / "conv" / "principals.json").write_text(json.dumps({
        "owners": [{"id": "ryan", "display_name": "Ryan",
                    "principals": {"telegram": "7191564227"}}]
    }))

    # ConvReadAdapter uses os.environ.get("USER") as the viewer; pin it to
    # the seeded owner so list_accessible_owners("ryan") returns {"ryan"}.
    monkeypatch.setenv("USER", "ryan")

    # Stub Keychain (no real `security` calls in tests)
    from flyn_memory_router.conv import encrypted_raw
    encrypted_raw._get_key.cache_clear()
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)

    return tmp_path


@pytest.fixture
def client(test_env):
    from flyn_memory_router.server import build_app
    return TestClient(build_app())


def _payload(text: str, msg_id: int = 100):
    return {
        "source": "telegram",
        "event_type": "conversation_message",
        "subject": f"tg-7191564227-{msg_id}",
        "body": text,
        "importance": "warm",
        "raw_payload": {
            "channel": "telegram",
            "chat_id": 7191564227,
            "sender_id": 7191564227,
            "thread_id": 7191564227,
            "reply_to_msg_id": None,
            "attachments": [],
            "ts": "2026-05-19T18:00:00+00:00",
        },
        "dedup_key": f"tg-7191564227-{msg_id}",
    }


def test_ingest_conv_message_writes_to_db(client, test_env):
    """POST → 200 → row exists in ryan.db."""
    resp = client.post("/api/memory/ingest", json=_payload("Linear backlog at 73 of 124"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert "conv" in body["tiers_written"]

    # Verify db row
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb("ryan", test_env / "conv" / "ryan.db")
    hits = db.search("Linear")
    assert len(hits) == 1
    assert "73 of 124" in hits[0].body


def test_query_returns_conv_hit(client, test_env):
    """After ingest, /api/memory/query includes conv hits via conv_read."""
    client.post("/api/memory/ingest", json=_payload("Pearl Platform launch this week"))

    resp = client.post("/api/memory/query", json={"q": "Pearl Platform", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    conv_hits = [h for h in body["hits"] if h["source"].startswith("conv/")]
    assert len(conv_hits) >= 1
    assert "Pearl" in conv_hits[0]["text"]


def test_unknown_sender_returns_accepted_false(client, test_env):
    """Telegram message from unmapped sender: 200 OK, accepted=False, tiers_written=[]."""
    payload = _payload("hello")
    payload["raw_payload"]["sender_id"] = 999999999  # unmapped
    resp = client.post("/api/memory/ingest", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is False
    assert body["tiers_written"] == []
    assert any("unknown sender" in note for note in body["notes"])
