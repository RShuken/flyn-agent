"""Summarize stage: call Ollama to populate messages.summary.

Includes short-circuit optimization: messages shorter than
SUMMARIZE_MIN_BODY_LEN bypass Ollama entirely (body is its own summary).
This was decision Q1 from the design doc.
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

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TIMEOUT = 30.0
SUMMARIZE_MIN_BODY_LEN = int(os.environ.get("FLYN_CONV_SUMMARIZE_MIN_LEN", "80"))

SUMMARY_PROMPT_TEMPLATE = (
    "Summarize this message in 1-2 sentences. Focus on what the sender "
    "said, decided, or asked. Skip pleasantries.\n\n"
    "Sender: {sender_id}\n"
    "Body: {body}\n\n"
    'Return JSON: {{"summary": "..."}}'
)


def _idempotency_key(message_id: int, body: str) -> str:
    """Stable key for Ollama dedup (informational; Ollama doesn't enforce it,
    but if we ever switch to a service that does, this is ready)."""
    h = hashlib.sha256(f"{message_id}:{body}".encode()).hexdigest()
    return h[:16]


def _call_ollama_sync(
    url: str, model: str, prompt: str, timeout: float
) -> str | None:
    """Blocking HTTP POST. Run via asyncio.to_thread from the async caller."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        parsed = json.loads(body.get("response", "").strip())
        summary = parsed.get("summary", "").strip()
        return summary if summary else None
    except Exception as exc:
        logger.warning("ollama call failed: %s", exc)
        return None


class SummarizeHandler:
    """Generate (or short-circuit) summary, write to messages.summary."""

    stage = Stage.SUMMARIZE

    def __init__(
        self,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        min_body_len: int = SUMMARIZE_MIN_BODY_LEN,
    ):
        self.ollama_url = ollama_url
        self.model = model
        self.timeout = timeout
        self.min_body_len = min_body_len

    async def handle(self, job: Job, db_path: Path) -> None:
        # 1. Read body + sender_id
        def _read() -> tuple[str, str, str | None]:
            with open_db(db_path) as conn:
                row = conn.execute(
                    "SELECT body, sender_id, summary FROM messages WHERE id = ?",
                    (job.message_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"message {job.message_id} not found")
                return row["body"], row["sender_id"], row["summary"]

        body, sender_id, existing_summary = await asyncio.to_thread(_read)

        # 2. Idempotent: if summary already set, skip
        if existing_summary:
            return

        # 3. Short-circuit for short messages — body IS its own summary
        if len(body) < self.min_body_len:
            summary = body
        else:
            # 4. Real Ollama call
            prompt = SUMMARY_PROMPT_TEMPLATE.format(
                body=body[:4000], sender_id=sender_id
            )
            summary = await asyncio.to_thread(
                _call_ollama_sync,
                self.ollama_url, self.model, prompt, self.timeout,
            )
            if summary is None:
                raise RuntimeError("ollama returned no summary")

        # 5. Write the summary + record idempotency key for the workflow row
        idem_key = _idempotency_key(job.message_id, body)

        def _write() -> None:
            with open_db(db_path) as conn:
                conn.execute(
                    "UPDATE messages SET summary = ? WHERE id = ?",
                    (summary, job.message_id),
                )
                conn.execute(
                    "UPDATE conversation_workflow "
                    "   SET idempotency_key_summarize = ? "
                    " WHERE message_id = ?",
                    (idem_key, job.message_id),
                )

        await asyncio.to_thread(_write)
