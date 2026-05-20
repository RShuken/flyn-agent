"""Background worker that pulls summarize-jobs from the disk queue and
calls Ollama's gemma4:e4b to fill `messages.summary` for conversation rows.

Reuses the existing memory-router queue dir convention. Each job is a
single JSON file under <queue_dir>/conv-summarize/ whose name doubles as
the unique job id. On success the file is deleted. On failure it stays
for the next poll. A daily backfill pulse (deploy/pulses/) scans for
rows with NULL summary older than 1h and re-enqueues them.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import ConvDb

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_TIMEOUT = 30.0
BUSY_POLL_S = 1.0
IDLE_POLL_S = 10.0
# Max times we'll re-try a single update_summary failure before moving the job
# to dead-letter. Prevents tight 1s spin when DB is broken (disk full,
# permission flip, owner DB deleted, etc).
MAX_SUMMARY_RETRIES = 3

SUMMARY_PROMPT_TEMPLATE = (
    "Summarize this Telegram message in 1-2 sentences. Focus on what the "
    "sender said, decided, or asked. Skip pleasantries.\n\n"
    "Sender: {sender_id}\n"
    "Body: {body}\n\n"
    'Return JSON: {{"summary": "..."}}'
)


@dataclass(frozen=True)
class SummarizeJob:
    """One queued job. Serialized as a JSON file on disk."""
    owner_id: str
    db_path: str
    row_id: int
    body: str
    sender_id: str

    def to_path(self, queue_dir: Path) -> Path:
        return queue_dir / f"conv-summarize-{self.owner_id}-{self.row_id}.json"

    @classmethod
    def from_file(cls, p: Path) -> "SummarizeJob":
        d = json.loads(p.read_text())
        return cls(**d)


def enqueue(queue_dir: Path, job: SummarizeJob) -> Path:
    """Write a SummarizeJob to disk. Returns the file path."""
    target = queue_dir / "conv-summarize"
    target.mkdir(parents=True, exist_ok=True)
    p = target / f"conv-summarize-{job.owner_id}-{job.row_id}.json"
    p.write_text(json.dumps(job.__dict__))
    return p


class SummarizerWorker:
    def __init__(
        self,
        queue_dir: Path,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._dir = queue_dir / "conv-summarize"
        self._url = ollama_url
        self._model = model
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._loop, name="conv-summarizer", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            processed = self._tick()
            self._stop.wait(BUSY_POLL_S if processed else IDLE_POLL_S)

    def _tick(self) -> bool:
        """Pull one job and process it. Returns True if a job was attempted."""
        jobs = sorted(
            (p for p in self._dir.glob("conv-summarize-*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        if not jobs:
            return False
        job_path = jobs[0]
        try:
            raw = json.loads(job_path.read_text())
            attempts = int(raw.pop("_attempts", 0))
            job = SummarizeJob(**raw)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("conv-summarize: bad job file %s: %s", job_path, exc)
            job_path.unlink(missing_ok=True)
            return True
        summary = self._call_ollama(job.body, job.sender_id)
        if summary is None:
            return True  # leave job in place for retry
        try:
            ConvDb(job.owner_id, Path(job.db_path)).update_summary(job.row_id, summary)
            job_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(
                "conv-summarize: update_summary failed (attempt %d/%d): %s",
                attempts + 1, MAX_SUMMARY_RETRIES, exc,
            )
            self._record_failure(job_path, job, attempts + 1)
        return True

    def _record_failure(self, job_path: Path, job: SummarizeJob, attempts: int) -> None:
        """Increment the in-file retry counter; move to dead-letter past max."""
        payload: dict[str, Any] = {**job.__dict__, "_attempts": attempts}
        if attempts >= MAX_SUMMARY_RETRIES:
            dead_dir = self._dir / "dead-letter"
            dead_dir.mkdir(parents=True, exist_ok=True)
            target = dead_dir / job_path.name
            target.write_text(json.dumps(payload))
            job_path.unlink(missing_ok=True)
            logger.warning(
                "conv-summarize: job %s exhausted %d retries, moved to dead-letter",
                job_path.name, MAX_SUMMARY_RETRIES,
            )
        else:
            job_path.write_text(json.dumps(payload))

    def _call_ollama(self, body: str, sender_id: str) -> str | None:
        prompt = SUMMARY_PROMPT_TEMPLATE.format(body=body[:4000], sender_id=sender_id)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        try:
            req = urllib.request.Request(
                self._url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body_resp = json.loads(resp.read())
            parsed = json.loads(body_resp.get("response", "").strip())
            summary = parsed.get("summary", "").strip()
            return summary if summary else None
        except Exception as exc:
            logger.debug("conv-summarize: ollama call failed: %s", exc)
            return None
