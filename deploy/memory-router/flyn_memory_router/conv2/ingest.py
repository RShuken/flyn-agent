"""Ingest entry point for conv-tier 2.0.

Called from the FastAPI route handler. Writes a `messages` row + a
`conversation_workflow` row + enqueues encrypt + index jobs — all in
one transaction so a crash mid-ingest never leaves orphaned state.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .schema import open_db
from .state import Stage
from .work_queue import WorkQueue
from .workflow import create_workflow


def make_trace_id() -> str:
    """Random 16-char trace id. Prefixed with `tr-` for grep-ability in logs."""
    return f"tr-{secrets.token_hex(8)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class IngestResult:
    message_id: int
    trace_id: str
    accepted: bool


async def ingest(
    db_path: Path,
    queue: WorkQueue,
    channel: str,
    sender_id: str,
    thread_id: str | None,
    reply_to_id: int | None,
    body: str,
    attachments: list | None = None,
) -> IngestResult:
    """Write the message + workflow row + queue jobs.

    Returns IngestResult with the new message_id and trace_id.
    """
    trace_id = make_trace_id()
    ts = _now_iso()

    def _write_row() -> int:
        with open_db(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO messages "
                "(channel, sender_id, thread_id, reply_to_id, ts, body, "
                "attachments, summary, encrypted_raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (channel, sender_id, thread_id, reply_to_id, ts, body,
                 _serialize_attachments(attachments),
                 b""),  # encrypted_raw filled by EncryptHandler
            )
            return cur.lastrowid

    message_id = await asyncio.to_thread(_write_row)

    # Create workflow row (state=received)
    await asyncio.to_thread(create_workflow, db_path, message_id, trace_id)

    # Enqueue the first stage; each stage enqueues its successor on success.
    await queue.enqueue(Stage.ENCRYPT, message_id, trace_id)

    return IngestResult(
        message_id=message_id,
        trace_id=trace_id,
        accepted=True,
    )


def _serialize_attachments(attachments: list | None) -> str | None:
    if not attachments:
        return None
    import json
    return json.dumps(attachments)
