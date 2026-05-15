from __future__ import annotations

from pathlib import Path

import pytest

from flyn_memory_router.dedup import DedupStore


@pytest.fixture
def store(tmp_path: Path) -> DedupStore:
    return DedupStore(db_path=tmp_path / "router.db")


def test_first_seen_returns_false(store: DedupStore):
    assert store.seen("orchestrator", "T-0042-created") is False


def test_record_then_seen(store: DedupStore):
    store.record("orchestrator", "T-0042-created")
    assert store.seen("orchestrator", "T-0042-created") is True


def test_namespaced_by_source(store: DedupStore):
    store.record("orchestrator", "msg-123")
    assert store.seen("telegram", "msg-123") is False
    assert store.seen("orchestrator", "msg-123") is True


def test_record_idempotent(store: DedupStore):
    store.record("a", "k")
    store.record("a", "k")
    assert store.seen("a", "k") is True


def test_init_creates_db_and_schema(tmp_path: Path):
    p = tmp_path / "sub" / "router.db"
    DedupStore(db_path=p)
    assert p.exists()
