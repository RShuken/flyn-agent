from __future__ import annotations

from pathlib import Path

import pytest

from flyn_memory_router.queue import EventQueue
from flyn_memory_router.types import InboundEvent


@pytest.fixture
def q(tmp_path: Path) -> EventQueue:
    return EventQueue(queue_dir=tmp_path)


def _e(k: str) -> InboundEvent:
    return InboundEvent(source="x", event_type="y", subject="s",
                        body="b" * 20, dedup_key=k)


def test_enqueue_creates_file(q: EventQueue, tmp_path: Path):
    q.enqueue(_e("k-1"))
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_drain_returns_in_order(q: EventQueue):
    q.enqueue(_e("a"))
    q.enqueue(_e("b"))
    q.enqueue(_e("c"))
    drained = [e.dedup_key for e in q.drain()]
    assert drained == ["a", "b", "c"]


def test_drain_empties_queue(q: EventQueue, tmp_path: Path):
    q.enqueue(_e("a"))
    list(q.drain())
    assert len(list(tmp_path.glob("*.json"))) == 0


def test_corrupt_file_is_quarantined(q: EventQueue, tmp_path: Path):
    bad = tmp_path / "001-bad.json"
    bad.write_text("not json")
    drained = list(q.drain())
    assert drained == []
    assert (tmp_path / "quarantine" / "001-bad.json").exists()
