"""Durable typed work queue for conv-tier 2.0.

Hybrid in-memory + SQLite-backed queue:

- **In-memory layer**: per-stage asyncio.Queue for sub-ms pickup latency.
  Workers `await queue.get()` — no polling, no idle CPU.
- **Persistent layer**: every enqueue writes a row to the `work_queue`
  table BEFORE notifying the in-memory queue. On crash recovery, on
  startup we scan for stale `in_flight_until` claims and re-enqueue.

Claim is atomic via `UPDATE...WHERE id = (SELECT...)` so multiple
workers can race without double-processing.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schema import open_db
from .state import Stage

logger = logging.getLogger(__name__)

# How long a worker can hold a claim before it's considered stale and re-claimable.
DEFAULT_CLAIM_TIMEOUT_S = 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now_plus_seconds(seconds: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


@dataclass(frozen=True)
class Job:
    """A claimed work item, ready for the worker to process."""

    id: int
    stage: Stage
    message_id: int
    trace_id: str
    attempts: int


class WorkQueue:
    """Per-stage durable async queue.

    Single instance manages all four stages. Each stage has its own
    asyncio.Queue and asyncio.Event for blocking pickup.
    """

    def __init__(self, db_path: Path, claim_timeout_s: int = DEFAULT_CLAIM_TIMEOUT_S):
        self._db_path = db_path
        self._claim_timeout_s = claim_timeout_s
        # Wake events: workers await wakers[stage] when their queue is empty
        self._wakers: dict[Stage, asyncio.Event] = {
            s: asyncio.Event() for s in Stage
        }

    def db_path(self) -> Path:
        return self._db_path

    async def enqueue(
        self,
        stage: Stage,
        message_id: int,
        trace_id: str,
        delay_seconds: int = 0,
    ) -> int:
        """Add a job to the queue. Returns the work_queue row id.

        Persists to SQLite before notifying. If delay_seconds > 0, the
        job becomes claimable at `now + delay` (used for exponential
        backoff after a retry).
        """
        now = _now()
        next_attempt_at = _now_plus_seconds(delay_seconds) if delay_seconds > 0 else now

        def _insert() -> int:
            with open_db(self._db_path) as conn:
                cur = conn.execute(
                    "INSERT INTO work_queue "
                    "(stage, message_id, trace_id, enqueued_at, next_attempt_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (stage.value, message_id, trace_id, now, next_attempt_at),
                )
                return cur.lastrowid

        job_id = await asyncio.to_thread(_insert)
        # Wake the worker(s) for this stage
        self._wakers[stage].set()
        return job_id

    async def claim_next(self, stage: Stage) -> Optional[Job]:
        """Atomically claim the oldest ready job for `stage`.

        Returns None if no claimable job (worker should await wait_for).
        Uses UPDATE...WHERE id = (SELECT...) RETURNING for atomic claim.
        """
        in_flight_until = _now_plus_seconds(self._claim_timeout_s)

        def _claim() -> Optional[Job]:
            with open_db(self._db_path) as conn:
                # SQLite 3.35+ supports RETURNING
                row = conn.execute(
                    "UPDATE work_queue "
                    "   SET in_flight_until = ?, attempts = attempts + 1 "
                    " WHERE id = (SELECT id FROM work_queue "
                    "              WHERE stage = ? "
                    "                AND (in_flight_until IS NULL "
                    "                     OR in_flight_until < datetime('now')) "
                    "                AND next_attempt_at <= datetime('now') "
                    "              ORDER BY enqueued_at ASC "
                    "              LIMIT 1) "
                    "RETURNING id, stage, message_id, trace_id, attempts",
                    (in_flight_until, stage.value),
                ).fetchone()
                if row is None:
                    return None
                return Job(
                    id=row["id"],
                    stage=Stage(row["stage"]),
                    message_id=row["message_id"],
                    trace_id=row["trace_id"],
                    attempts=row["attempts"],
                )

        return await asyncio.to_thread(_claim)

    async def complete(self, job: Job) -> None:
        """Mark a job as successfully processed — remove from queue."""
        def _delete() -> None:
            with open_db(self._db_path) as conn:
                conn.execute("DELETE FROM work_queue WHERE id = ?", (job.id,))

        await asyncio.to_thread(_delete)

    async def fail(
        self,
        job: Job,
        error: str,
        backoff_seconds: int = 2,
        max_attempts: int = 3,
    ) -> bool:
        """Mark a job as failed.

        Returns True if job was moved to dead-letter (max attempts reached).
        Otherwise re-enqueues with exponential backoff and returns False.
        """
        def _process() -> bool:
            with open_db(self._db_path) as conn:
                if job.attempts >= max_attempts:
                    # Move to dead-letter
                    conn.execute(
                        "INSERT INTO dead_letter_queue "
                        "(stage, message_id, trace_id, attempts, last_error, failed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (job.stage.value, job.message_id, job.trace_id,
                         job.attempts, error, _now()),
                    )
                    conn.execute("DELETE FROM work_queue WHERE id = ?", (job.id,))
                    return True
                # Re-enqueue with backoff
                conn.execute(
                    "UPDATE work_queue "
                    "   SET in_flight_until = NULL, next_attempt_at = ? "
                    " WHERE id = ?",
                    (_now_plus_seconds(backoff_seconds), job.id),
                )
                return False

        moved_to_dlq = await asyncio.to_thread(_process)
        # Wake the worker so it sees the re-enqueued job after backoff
        self._wakers[job.stage].set()
        return moved_to_dlq

    async def depth(self, stage: Stage) -> int:
        """Number of jobs currently claimable or in-flight for `stage`."""
        def _count() -> int:
            with open_db(self._db_path) as conn:
                return conn.execute(
                    "SELECT count(*) FROM work_queue WHERE stage = ?",
                    (stage.value,),
                ).fetchone()[0]
        return await asyncio.to_thread(_count)

    async def total_depth(self) -> int:
        """Total jobs across all stages — used by backpressure check."""
        def _count() -> int:
            with open_db(self._db_path) as conn:
                return conn.execute("SELECT count(*) FROM work_queue").fetchone()[0]
        return await asyncio.to_thread(_count)

    async def dead_letter_count(self) -> int:
        """Total dead-letter entries — for the health endpoint."""
        def _count() -> int:
            with open_db(self._db_path) as conn:
                return conn.execute("SELECT count(*) FROM dead_letter_queue").fetchone()[0]
        return await asyncio.to_thread(_count)

    async def wait_for(self, stage: Stage, timeout: Optional[float] = None) -> None:
        """Block until a job is enqueued for `stage` (or timeout)."""
        waker = self._wakers[stage]
        try:
            await asyncio.wait_for(waker.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            waker.clear()

    def notify(self, stage: Stage) -> None:
        """Wake any worker waiting on this stage. Synchronous (no I/O)."""
        self._wakers[stage].set()

    async def reclaim_stale(self) -> int:
        """Reclaim any in-flight claims that expired (crash recovery).

        Returns the number of jobs re-enqueued. Called at startup.
        """
        def _reclaim() -> int:
            with open_db(self._db_path) as conn:
                cur = conn.execute(
                    "UPDATE work_queue "
                    "   SET in_flight_until = NULL "
                    " WHERE in_flight_until IS NOT NULL "
                    "   AND in_flight_until < datetime('now')"
                )
                return cur.rowcount

        n = await asyncio.to_thread(_reclaim)
        if n > 0:
            logger.info("crash recovery: reclaimed %d stale in-flight jobs", n)
            # Wake every stage in case any reclaimed jobs are now ready
            for waker in self._wakers.values():
                waker.set()
        return n
