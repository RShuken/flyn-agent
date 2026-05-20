"""Unit tests for per-stage handlers with mocked external services."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from flyn_memory_router.conv2 import encrypted_raw
from flyn_memory_router.conv2.handlers.encrypt import EncryptHandler
from flyn_memory_router.conv2.handlers.index import IndexHandler
from flyn_memory_router.conv2.handlers.promote import PromoteHandler
from flyn_memory_router.conv2.handlers.summarize import SummarizeHandler
from flyn_memory_router.conv2.schema import migrate, open_db
from flyn_memory_router.conv2.state import Stage
from flyn_memory_router.conv2.work_queue import Job


@pytest.fixture
def db_with_message(tmp_path: Path) -> tuple[Path, int]:
    """A migrated DB with one message row + a workflow row ready for handlers."""
    db = tmp_path / "owner.db"
    migrate(db)
    with open_db(db) as conn:
        cur = conn.execute(
            "INSERT INTO messages "
            "(channel, sender_id, thread_id, ts, body, encrypted_raw) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("telegram", "7191564227", "7191564227",
             "2026-05-19T22:00:00Z", "test body content", b""),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO conversation_workflow "
            "(message_id, state, trace_id, created_at) "
            "VALUES (?, 'received', 'tr-test', datetime('now'))",
            (mid,),
        )
    return db, mid


def _job(stage: Stage, message_id: int) -> Job:
    return Job(id=1, stage=stage, message_id=message_id, trace_id="tr-test", attempts=1)


# -------------------- EncryptHandler --------------------


@pytest.mark.asyncio
async def test_encrypt_handler_writes_ciphertext(db_with_message, monkeypatch):
    """EncryptHandler.handle calls seal and writes encrypted_raw."""
    db, mid = db_with_message
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"0123456789abcdef")
    h = EncryptHandler(owner_id="ryan")
    await h.handle(_job(Stage.ENCRYPT, mid), db)
    with open_db(db) as conn:
        row = conn.execute(
            "SELECT encrypted_raw FROM messages WHERE id = ?", (mid,)
        ).fetchone()
        assert row["encrypted_raw"] is not None
        assert len(row["encrypted_raw"]) > 0


@pytest.mark.asyncio
async def test_encrypt_handler_idempotent(db_with_message, monkeypatch):
    """If already encrypted, skip re-encryption (avoid wasted CPU)."""
    db, mid = db_with_message
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"0123456789abcdef")
    with open_db(db) as conn:
        conn.execute(
            "UPDATE messages SET encrypted_raw = ? WHERE id = ?",
            (b"already-encrypted-bytes", mid),
        )
    h = EncryptHandler(owner_id="ryan")
    await h.handle(_job(Stage.ENCRYPT, mid), db)
    with open_db(db) as conn:
        row = conn.execute(
            "SELECT encrypted_raw FROM messages WHERE id = ?", (mid,)
        ).fetchone()
        # Still the original "already-encrypted-bytes" — handler did not overwrite
        assert row["encrypted_raw"] == b"already-encrypted-bytes"


# -------------------- IndexHandler --------------------


@pytest.mark.asyncio
async def test_index_handler_noop_when_trigger_indexed(db_with_message):
    """IndexHandler is idempotent: if FTS row exists (via trigger), it's a no-op."""
    db, mid = db_with_message
    # Insert was already through the trigger, so messages_fts has the row
    h = IndexHandler()
    await h.handle(_job(Stage.INDEX, mid), db)
    # Verify FTS row present (no double-insertion error)
    with open_db(db) as conn:
        count = conn.execute(
            "SELECT count(*) FROM messages_fts WHERE rowid = ?", (mid,)
        ).fetchone()[0]
        assert count == 1


# -------------------- SummarizeHandler --------------------


@pytest.mark.asyncio
async def test_summarize_short_circuit_for_short_message(db_with_message):
    """Messages shorter than min_body_len use body as summary (no Ollama call)."""
    db, mid = db_with_message  # body is "test body content" — 17 chars
    h = SummarizeHandler(min_body_len=80)
    # Should NOT call Ollama; if it did, this test would hang or fail
    with patch("flyn_memory_router.conv2.handlers.summarize._call_ollama_sync") as mock:
        await h.handle(_job(Stage.SUMMARIZE, mid), db)
        mock.assert_not_called()
    with open_db(db) as conn:
        row = conn.execute(
            "SELECT summary FROM messages WHERE id = ?", (mid,)
        ).fetchone()
        assert row["summary"] == "test body content"


@pytest.mark.asyncio
async def test_summarize_calls_ollama_for_long_message(tmp_path: Path):
    """Messages longer than min_body_len go through Ollama."""
    db = tmp_path / "owner.db"
    migrate(db)
    long_body = "x" * 200  # well over 80 chars
    with open_db(db) as conn:
        cur = conn.execute(
            "INSERT INTO messages (channel, sender_id, ts, body, encrypted_raw) "
            "VALUES (?, ?, ?, ?, ?)",
            ("t", "x", "ts", long_body, b""),
        )
        mid = cur.lastrowid
    h = SummarizeHandler(min_body_len=80)
    with patch(
        "flyn_memory_router.conv2.handlers.summarize._call_ollama_sync",
        return_value="MOCKED SUMMARY",
    ) as mock:
        await h.handle(_job(Stage.SUMMARIZE, mid), db)
        mock.assert_called_once()
    with open_db(db) as conn:
        row = conn.execute(
            "SELECT summary FROM messages WHERE id = ?", (mid,)
        ).fetchone()
        assert row["summary"] == "MOCKED SUMMARY"


@pytest.mark.asyncio
async def test_summarize_raises_when_ollama_fails(tmp_path: Path):
    """If Ollama returns None, raise so the worker retries."""
    db = tmp_path / "owner.db"
    migrate(db)
    with open_db(db) as conn:
        cur = conn.execute(
            "INSERT INTO messages (channel, sender_id, ts, body, encrypted_raw) "
            "VALUES (?, ?, ?, ?, ?)",
            ("t", "x", "ts", "x" * 200, b""),
        )
        mid = cur.lastrowid
    h = SummarizeHandler(min_body_len=80)
    with patch(
        "flyn_memory_router.conv2.handlers.summarize._call_ollama_sync",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="ollama returned no summary"):
            await h.handle(_job(Stage.SUMMARIZE, mid), db)


@pytest.mark.asyncio
async def test_summarize_skips_when_summary_already_set(db_with_message):
    """Idempotent: if summary is set, handler does nothing (no Ollama call)."""
    db, mid = db_with_message
    with open_db(db) as conn:
        conn.execute("UPDATE messages SET summary = 'preset' WHERE id = ?", (mid,))
    h = SummarizeHandler(min_body_len=10)
    with patch(
        "flyn_memory_router.conv2.handlers.summarize._call_ollama_sync"
    ) as mock:
        await h.handle(_job(Stage.SUMMARIZE, mid), db)
        mock.assert_not_called()
    with open_db(db) as conn:
        row = conn.execute("SELECT summary FROM messages WHERE id = ?", (mid,)).fetchone()
        assert row["summary"] == "preset"


# -------------------- PromoteHandler --------------------


@pytest.mark.asyncio
async def test_promote_handler_posts_episode(db_with_message):
    """PromoteHandler POSTs an episode with the correct payload + idempotency key."""
    db, mid = db_with_message
    h = PromoteHandler(graphiti_url="http://localhost:8100", owner_id="ryan")
    with patch(
        "flyn_memory_router.conv2.handlers.promote._post_episode_sync",
        return_value=(200, "ok"),
    ) as mock:
        await h.handle(_job(Stage.PROMOTE, mid), db)
        mock.assert_called_once()
        url, payload, _timeout = mock.call_args[0]
        assert "/api/episode" in url and "/api/episodes" not in url
        assert payload["group_id"] == "flyn-ryan"
        assert "episode_id" in payload
        assert payload["metadata"]["message_id"] == mid


@pytest.mark.asyncio
async def test_promote_handler_raises_on_4xx(db_with_message):
    """Non-2xx Graphiti response raises so the worker retries."""
    db, mid = db_with_message
    h = PromoteHandler()
    with patch(
        "flyn_memory_router.conv2.handlers.promote._post_episode_sync",
        return_value=(500, "internal error"),
    ):
        with pytest.raises(RuntimeError, match="graphiti POST returned 500"):
            await h.handle(_job(Stage.PROMOTE, mid), db)
