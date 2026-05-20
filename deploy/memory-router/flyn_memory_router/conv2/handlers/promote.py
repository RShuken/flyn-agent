"""Promote stage: POST a Graphiti episode for the message.

Uses the idempotency_key as the episode UUID so Graphiti dedupes
naturally on retry (per design decision Q4 + the idempotency section).
The episode body includes the redacted body + summary; the encrypted_raw
blob stays in SQLite (Graphiti gets only entity-extractable text).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import urllib.request
from pathlib import Path

from ..schema import open_db
from ..state import Stage
from ..work_queue import Job

logger = logging.getLogger(__name__)

DEFAULT_GRAPHITI_URL = "http://localhost:8100"
# Graphiti runs LLM-based entity extraction per episode synchronously.
# Real-world latency: 60–120s warm, 180s worst-case. v1 conv_write used
# httpx.Timeout(180); we match that. End-to-end p99 will reflect Graphiti
# latency (not pipeline overhead) — pipeline-only p99 is ~50ms.
DEFAULT_TIMEOUT = 180.0


def _idempotency_key(message_id: int, channel: str, thread_id: str | None) -> str:
    h = hashlib.sha256(f"{channel}:{thread_id}:{message_id}".encode()).hexdigest()
    return h[:16]


def _post_episode_sync(
    url: str, payload: dict, timeout: float
) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")[:500]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")[:500]


class PromoteHandler:
    """POST a Graphiti episode for entity extraction."""

    stage = Stage.PROMOTE

    def __init__(
        self,
        graphiti_url: str = DEFAULT_GRAPHITI_URL,
        timeout: float = DEFAULT_TIMEOUT,
        owner_id: str = "ryan",
    ):
        self.graphiti_url = graphiti_url.rstrip("/")
        self.timeout = timeout
        self.owner_id = owner_id

    async def handle(self, job: Job, db_path: Path) -> None:
        # Read message + workflow metadata
        def _read() -> tuple[str, str, str, str | None, str | None]:
            with open_db(db_path) as conn:
                row = conn.execute(
                    "SELECT m.channel, m.thread_id, m.body, m.summary, m.ts "
                    "FROM messages m WHERE m.id = ?",
                    (job.message_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"message {job.message_id} not found")
                return (row["channel"], row["thread_id"] or "",
                        row["body"], row["summary"], row["ts"])

        channel, thread_id, body, summary, ts = await asyncio.to_thread(_read)

        # Compose episode body — summary if available, else body
        episode_body = summary or body
        idem_key = _idempotency_key(job.message_id, channel, thread_id)

        payload = {
            "name": f"conv-{channel}-{job.message_id}",
            "body": episode_body,
            "group_id": f"flyn-{self.owner_id}",
            "reference_time": ts,
            "episode_id": idem_key,  # Graphiti uses this for dedup
            "metadata": {
                "channel": channel,
                "thread_id": thread_id,
                "message_id": job.message_id,
                "trace_id": job.trace_id,
            },
        }

        # Graphiti server: POST /api/episode (singular) for writes.
        # GET /api/episodes (plural) is the read-only listing endpoint.
        status, response_text = await asyncio.to_thread(
            _post_episode_sync,
            f"{self.graphiti_url}/api/episode",
            payload,
            self.timeout,
        )

        if status >= 400:
            raise RuntimeError(
                f"graphiti POST returned {status}: {response_text[:200]}"
            )

        # Record idempotency key on workflow row for replay safety
        def _write_key() -> None:
            with open_db(db_path) as conn:
                conn.execute(
                    "UPDATE conversation_workflow "
                    "   SET idempotency_key_promote = ? "
                    " WHERE message_id = ?",
                    (idem_key, job.message_id),
                )

        await asyncio.to_thread(_write_key)
