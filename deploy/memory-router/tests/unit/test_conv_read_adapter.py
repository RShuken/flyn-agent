"""ConvReadAdapter — Protocol compliance + cross-owner audit."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


def _seed_registry(tmp_path: Path):
    from flyn_memory_router.conv.owner import OwnerRegistry
    p = tmp_path / "principals.json"
    p.write_text(json.dumps({
        "owners": [
            {"id": "ryan", "display_name": "Ryan", "principals": {"telegram": "7191564227"}},
            {"id": "beth", "display_name": "Beth", "principals": {"telegram": "7434192034"}},
        ]
    }))
    return OwnerRegistry(owners_db_path=tmp_path / "owners.db", principals_json=p)


def _seed_msg(tmp_path: Path, owner_id: str, body: str):
    from flyn_memory_router.conv.schema import ConvDb, ConvMessage
    db = ConvDb(owner_id, tmp_path / "conv" / f"{owner_id}.db")
    db.write(ConvMessage(
        channel="telegram", sender_id="x", thread_id="t", reply_to_id=None,
        ts="2026-05-19T18:00:00+00:00", body=body, attachments=[],
        encrypted_raw=b"\x00" * 32,
    ))


def test_read_adapter_protocol_compliance(tmp_path: Path):
    """Implements ReadAdapter Protocol: name, read_timeout, default_included, async query."""
    from flyn_memory_router.adapters.conv_read import ConvReadAdapter
    from flyn_memory_router.adapters.base import ReadAdapter
    adapter = ConvReadAdapter(
        registry=_seed_registry(tmp_path),
        conv_root=tmp_path / "conv",
        viewer_id="ryan",
    )
    assert adapter.name == "conv"
    assert adapter.read_timeout == 1.5
    assert adapter.default_included is True
    assert isinstance(adapter, ReadAdapter)

    _seed_msg(tmp_path, "ryan", "Linear backlog discussion")
    hits = asyncio.run(adapter.query("linear", top_k=5))
    assert len(hits) == 1
    assert hits[0].source == "conv/telegram"
    assert hits[0].metadata["owner"] == "ryan"


def test_cross_owner_read_writes_audit(tmp_path: Path):
    """Reading another owner's data (with grant) writes to audit_log."""
    from flyn_memory_router.adapters.conv_read import ConvReadAdapter
    registry = _seed_registry(tmp_path)
    registry.grant("ryan", "beth", granted_by="ryan", reason="testing")

    _seed_msg(tmp_path, "beth", "Beth said something about Pearl Platform")
    adapter = ConvReadAdapter(
        registry=registry,
        conv_root=tmp_path / "conv",
        viewer_id="ryan",
    )
    hits = asyncio.run(adapter.query("pearl platform", top_k=5))
    assert any(h.metadata["owner"] == "beth" for h in hits)

    # An audit row should exist for the cross-owner read
    audit = registry.recent_audit(limit=10)
    assert any(r["viewer"] == "ryan" and r["owned_by"] == "beth" and r["op"] == "read"
               for r in audit)
