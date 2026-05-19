"""ConvWriteAdapter — happy path + 2 failure modes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _registry(tmp_path: Path):
    from flyn_memory_router.conv.owner import OwnerRegistry
    principals = tmp_path / "principals.json"
    principals.write_text(json.dumps({
        "owners": [{"id": "ryan", "display_name": "Ryan",
                    "principals": {"telegram": "7191564227"}}]
    }))
    return OwnerRegistry(owners_db_path=tmp_path / "owners.db",
                         principals_json=principals)


def _event(**raw_overrides):
    from flyn_memory_router.types import InboundEvent
    raw = dict(
        channel="telegram",
        chat_id=7191564227,
        sender_id=7191564227,
        thread_id=7191564227,
        reply_to_msg_id=None,
        attachments=[],
        ts="2026-05-19T18:00:00+00:00",
    )
    raw.update(raw_overrides)
    return InboundEvent(
        source="telegram",
        event_type="conversation_message",
        subject="tg-7191564227-100",
        body="Linear backlog stuck at 73 of 124",
        importance="warm",
        raw_payload=raw,
        valid_at=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
        dedup_key="tg-7191564227-100",
    )


def test_happy_path_writes_row(tmp_path: Path, monkeypatch):
    """Resolves owner → seals raw → writes message → returns ok=True."""
    from flyn_memory_router.adapters.conv_write import ConvWriteAdapter
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)

    adapter = ConvWriteAdapter(
        registry=_registry(tmp_path),
        conv_root=tmp_path / "conv",
        queue_dir=tmp_path / "queue",
        graphiti_url=None,
    )
    result = adapter.write(_event())
    assert result.ok is True
    # Verify row landed in ryan.db
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb("ryan", tmp_path / "conv" / "ryan.db")
    hits = db.search("Linear backlog")
    assert len(hits) == 1


def test_unknown_sender_returns_ok_false(tmp_path: Path, monkeypatch):
    """Sender with no principal mapping → ok=False, no row written."""
    from flyn_memory_router.adapters.conv_write import ConvWriteAdapter
    from flyn_memory_router.conv import encrypted_raw
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)

    adapter = ConvWriteAdapter(
        registry=_registry(tmp_path),
        conv_root=tmp_path / "conv",
        queue_dir=tmp_path / "queue",
        graphiti_url=None,
    )
    result = adapter.write(_event(sender_id=999999999))
    assert result.ok is False
    assert "unknown sender" in result.detail.lower()
    assert not (tmp_path / "conv" / "ryan.db").exists()


def test_keychain_locked_returns_ok_false(tmp_path: Path, monkeypatch):
    """seal raises KeychainLocked → ok=False, row NOT stored unencrypted."""
    from flyn_memory_router.adapters.conv_write import ConvWriteAdapter
    from flyn_memory_router.conv import encrypted_raw

    def fail(*args, **kwargs):
        raise encrypted_raw.KeychainLocked("locked")
    monkeypatch.setattr(encrypted_raw, "seal", fail)

    adapter = ConvWriteAdapter(
        registry=_registry(tmp_path),
        conv_root=tmp_path / "conv",
        queue_dir=tmp_path / "queue",
        graphiti_url=None,
    )
    result = adapter.write(_event())
    assert result.ok is False
    assert "keychain" in result.detail.lower()
