"""Durable async work queue tests."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from flyn_memory_router.conv2.schema import migrate, open_db
from flyn_memory_router.conv2.state import Stage
from flyn_memory_router.conv2.work_queue import Job, WorkQueue


@pytest.fixture
async def queue(tmp_path: Path) -> WorkQueue:
    db = tmp_path / "owner.db"
    migrate(db)
    return WorkQueue(db_path=db, claim_timeout_s=2)


@pytest.mark.asyncio
async def test_enqueue_persists_to_sqlite(tmp_path: Path):
    """Enqueue writes to work_queue table before returning."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    job_id = await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="tr-a")
    assert job_id > 0
    # Verify durable
    with open_db(db) as conn:
        rows = conn.execute(
            "SELECT * FROM work_queue WHERE id = ?", (job_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["stage"] == "encrypt"
        assert rows[0]["message_id"] == 1


@pytest.mark.asyncio
async def test_claim_returns_oldest_job(tmp_path: Path):
    """Claim returns the oldest enqueued job for the stage."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    id_a = await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="a")
    await asyncio.sleep(0.01)
    id_b = await q.enqueue(Stage.ENCRYPT, message_id=2, trace_id="b")
    job = await q.claim_next(Stage.ENCRYPT)
    assert job is not None
    assert job.id == id_a
    assert job.message_id == 1


@pytest.mark.asyncio
async def test_claim_returns_none_when_empty(tmp_path: Path):
    """Empty queue returns None — caller should block on wait_for."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    job = await q.claim_next(Stage.SUMMARIZE)
    assert job is None


@pytest.mark.asyncio
async def test_claim_does_not_double_pickup(tmp_path: Path):
    """Two simultaneous claims for the same stage produce one winner."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="a")
    # Two concurrent claims
    j1, j2 = await asyncio.gather(
        q.claim_next(Stage.ENCRYPT), q.claim_next(Stage.ENCRYPT)
    )
    # One gets the job, the other gets None (already in-flight)
    assert (j1 is None) ^ (j2 is None)


@pytest.mark.asyncio
async def test_complete_removes_from_queue(tmp_path: Path):
    """complete() deletes the row so it's no longer claimable."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="a")
    job = await q.claim_next(Stage.ENCRYPT)
    await q.complete(job)
    assert await q.claim_next(Stage.ENCRYPT) is None
    assert await q.depth(Stage.ENCRYPT) == 0


@pytest.mark.asyncio
async def test_fail_below_max_retries(tmp_path: Path):
    """fail() with attempts < max re-enqueues with backoff, returns False."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    await q.enqueue(Stage.SUMMARIZE, message_id=1, trace_id="a")
    job = await q.claim_next(Stage.SUMMARIZE)
    moved_to_dlq = await q.fail(job, "transient", backoff_seconds=0, max_attempts=3)
    assert moved_to_dlq is False
    # Job still in queue, but next_attempt_at is in the past so we can claim it again
    j2 = await q.claim_next(Stage.SUMMARIZE)
    assert j2 is not None
    assert j2.id == job.id
    assert j2.attempts == 2


@pytest.mark.asyncio
async def test_fail_at_max_retries_moves_to_dead_letter(tmp_path: Path):
    """fail() with attempts >= max moves to dead_letter_queue and removes from work_queue."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    await q.enqueue(Stage.SUMMARIZE, message_id=42, trace_id="tr-x")
    # Burn through 3 attempts
    for _ in range(3):
        job = await q.claim_next(Stage.SUMMARIZE)
        assert job is not None
        moved = await q.fail(job, "err", backoff_seconds=0, max_attempts=3)
    assert moved is True
    # Gone from work_queue, present in dead_letter_queue
    assert await q.claim_next(Stage.SUMMARIZE) is None
    assert await q.dead_letter_count() == 1


@pytest.mark.asyncio
async def test_wait_for_blocks_until_notify(tmp_path: Path):
    """wait_for blocks; enqueue wakes it via the notify event."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)

    async def waiter():
        t0 = time.monotonic()
        await q.wait_for(Stage.ENCRYPT, timeout=2.0)
        return time.monotonic() - t0

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)  # let waiter block
    await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="a")
    elapsed = await task
    assert elapsed < 0.5, f"wait_for should wake quickly on enqueue; took {elapsed}s"


@pytest.mark.asyncio
async def test_pickup_latency_under_50ms(tmp_path: Path):
    """Critical SLO: queue pickup must be < 50ms when a job is enqueued."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)

    async def worker_sim():
        while True:
            job = await q.claim_next(Stage.ENCRYPT)
            if job is not None:
                return time.monotonic()
            await q.wait_for(Stage.ENCRYPT, timeout=1.0)

    t_start = time.monotonic()
    task = asyncio.create_task(worker_sim())
    await asyncio.sleep(0.01)
    await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="a")
    t_claimed = await task
    pickup_ms = (t_claimed - t_start) * 1000
    assert pickup_ms < 50, f"pickup latency {pickup_ms}ms exceeds 50ms SLO"


@pytest.mark.asyncio
async def test_reclaim_stale_recovers_inflight_jobs(tmp_path: Path):
    """Stale in_flight_until claims (crashed worker) are re-enqueued on reclaim."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db, claim_timeout_s=1)
    await q.enqueue(Stage.ENCRYPT, message_id=1, trace_id="a")
    job = await q.claim_next(Stage.ENCRYPT)
    assert job is not None
    # Worker "crashes" — never calls complete or fail
    # Wait for claim to go stale
    await asyncio.sleep(1.5)
    reclaimed = await q.reclaim_stale()
    assert reclaimed == 1
    # Now claimable again
    re_job = await q.claim_next(Stage.ENCRYPT)
    assert re_job is not None
    assert re_job.message_id == 1


@pytest.mark.asyncio
async def test_depth_and_total_depth(tmp_path: Path):
    """Per-stage and aggregate queue depth counters work."""
    db = tmp_path / "owner.db"
    migrate(db)
    q = WorkQueue(db_path=db)
    await q.enqueue(Stage.ENCRYPT, 1, "a")
    await q.enqueue(Stage.ENCRYPT, 2, "b")
    await q.enqueue(Stage.SUMMARIZE, 3, "c")
    assert await q.depth(Stage.ENCRYPT) == 2
    assert await q.depth(Stage.SUMMARIZE) == 1
    assert await q.depth(Stage.PROMOTE) == 0
    assert await q.total_depth() == 3
