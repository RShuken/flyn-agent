"""Index stage: ensure the FTS5 row exists for the message.

The v1 schema (`conv/schema.py`) already creates an AFTER INSERT trigger
that auto-populates the FTS5 index. We keep the index stage as a no-op
when the trigger has already done the work; this stage exists as an
explicit pipeline checkpoint so the workflow row records indexed_at.

If the v1 trigger isn't present (e.g., a v2-only deployment), this
handler inserts the FTS5 row directly.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..schema import open_db
from ..state import Stage
from ..work_queue import Job


class IndexHandler:
    """Idempotent FTS5 index check. The trigger usually does the actual
    work; this handler verifies + writes if missing."""

    stage = Stage.INDEX

    async def handle(self, job: Job, db_path: Path) -> None:
        def _work() -> None:
            with open_db(db_path) as conn:
                # Check if FTS5 table exists (v1 schema)
                fts_exists = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='messages_fts'"
                ).fetchone() is not None
                if not fts_exists:
                    # v2-only deployment without FTS5: nothing to do
                    return
                # Check if the row is already indexed
                row = conn.execute(
                    "SELECT rowid FROM messages_fts WHERE rowid = ?",
                    (job.message_id,),
                ).fetchone()
                if row is not None:
                    return  # Already indexed by trigger
                # Backfill if missing (rare; usually trigger handles it)
                msg = conn.execute(
                    "SELECT body, summary FROM messages WHERE id = ?",
                    (job.message_id,),
                ).fetchone()
                if msg is None:
                    raise RuntimeError(f"message {job.message_id} not found")
                conn.execute(
                    "INSERT INTO messages_fts(rowid, body, summary) VALUES (?, ?, ?)",
                    (job.message_id, msg["body"], msg["summary"] or ""),
                )

        await asyncio.to_thread(_work)
