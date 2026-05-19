"""ConvDb — write, search, thread queries, summary update with FTS5 sync."""
from __future__ import annotations

from pathlib import Path


def _msg(**overrides):
    from flyn_memory_router.conv.schema import ConvMessage
    base = dict(
        channel="telegram",
        sender_id="7191564227",
        thread_id="7191564227",
        reply_to_id=None,
        ts="2026-05-19T18:00:00+00:00",
        body="hello world",
        attachments=[],
        encrypted_raw=b"\x00" * 32,
    )
    base.update(overrides)
    return ConvMessage(**base)


def test_write_then_search_roundtrip(tmp_path: Path):
    """Write a message; FTS5 finds it by body content."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    row_id = db.write(_msg(body="Linear backlog stuck at 73 of 124"))
    assert row_id > 0
    hits = db.search("linear backlog", top_k=5)
    assert len(hits) == 1
    assert hits[0].row_id == row_id
    assert "Linear" in hits[0].body


def test_thread_query_returns_chronological(tmp_path: Path):
    """get_by_thread returns messages in ts DESC order, limited."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    for i in range(5):
        db.write(_msg(
            ts=f"2026-05-19T10:{i:02d}:00+00:00",
            body=f"message {i}",
            thread_id="t1",
        ))
    out = db.get_by_thread("t1", limit=3)
    assert len(out) == 3
    assert out[0].body == "message 4"  # newest first
    assert out[2].body == "message 2"


def test_summary_update_indexes_in_fts(tmp_path: Path):
    """update_summary updates messages table AND propagates to FTS5."""
    from flyn_memory_router.conv.schema import ConvDb
    db = ConvDb(owner_id="ryan", path=tmp_path / "ryan.db")
    row_id = db.write(_msg(body="opaque body text X"))
    # Initially the summary is NULL — searching for a summary-only token misses
    assert db.search("revenue figures", top_k=5) == []
    db.update_summary(row_id, "Discussion of revenue figures for Q2")
    hits = db.search("revenue figures", top_k=5)
    assert len(hits) == 1
    assert hits[0].row_id == row_id
    assert hits[0].summary is not None
