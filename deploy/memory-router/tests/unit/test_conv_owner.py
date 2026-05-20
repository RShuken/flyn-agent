"""OwnerRegistry — resolution, grants, default-deny, audit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def registry(tmp_path: Path):
    """Build a registry with one owner seeded."""
    from flyn_memory_router.conv.owner import OwnerRegistry
    owners_db = tmp_path / "owners.db"
    principals = tmp_path / "principals.json"
    principals.write_text(json.dumps({
        "owners": [
            {"id": "ryan", "display_name": "Ryan Shuken",
             "principals": {"telegram": "7191564227"}}
        ]
    }))
    return OwnerRegistry(owners_db_path=owners_db, principals_json=principals)


def test_self_read_allowed(registry):
    """viewer == owner: always allowed. resolve_from_chat finds seeded owner."""
    assert registry.viewer_can_read("ryan", "ryan") is True
    owner = registry.resolve_from_chat("telegram", "7191564227")
    assert owner is not None
    assert owner.id == "ryan"


def test_default_deny_cross_owner_read(registry):
    """No grant → viewer cannot read another owner's data."""
    assert registry.viewer_can_read("beth", "ryan") is False
    assert registry.list_accessible_owners("beth") == set()


def test_grant_allows_read_and_writes_audit(registry):
    """grant() persists; subsequent reads write an audit row."""
    registry.grant("beth", "ryan", granted_by="ryan", reason="OL planning")
    assert registry.viewer_can_read("beth", "ryan") is True
    registry.append_audit("beth", "ryan", op="read", q="linear backlog")
    rows = registry.recent_audit(limit=5)
    assert any(r["viewer"] == "beth" and r["owned_by"] == "ryan" and r["op"] == "read"
               for r in rows)
