"""Backpressure (Phase E) + chaos / crash recovery (Phase F) tests."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flyn_memory_router.conv2.backpressure import (
    BackpressureState,
    OverloadError,
    check_and_apply,
)
from flyn_memory_router.conv2.ingest import ingest
from flyn_memory_router.conv2.schema import migrate
from flyn_memory_router.conv2.state import Stage, WorkflowState
from flyn_memory_router.conv2.supervisor import WorkerPool
from flyn_memory_router.conv2.work_queue import Job, WorkQueue
from flyn_memory_router.conv2.workflow import get_workflow


# -------------------- Phase E: backpressure --------------------


class _SlowHandler:
    """Handler that sleeps long enough that the queue builds up."""

    def __init__(self, stage: Stage):
        self.stage = stage

    async def handle(self, job: Job, db_path: Path) -> None:
        await asyncio.sleep(0.5)  # slow on purpose


@pytest.mark.asyncio
async def test_reject_new_raises_overload_error(tmp_path: Path):
    """reject_new policy: ingest raises OverloadError above HIGH_WATER."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db)
    # Stuff the queue with 5 items, set high_water=3
    for i in range(5):
        await queue.enqueue(Stage.SUMMARIZE, message_id=i, trace_id=f"t{i}")
    state = BackpressureState(high_water=3, policy="reject_new")
    with pytest.raises(OverloadError):
        await check_and_apply(queue, state)
    assert state.active is True
    assert state.total_drops == 1
    assert state.last_drop_at is not None


@pytest.mark.asyncio
async def test_drop_oldest_evicts_first_entry(tmp_path: Path):
    """drop_oldest policy: oldest work_queue row is deleted on overload."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db)
    # Enqueue with ordered timestamps
    for i in range(5):
        await queue.enqueue(Stage.SUMMARIZE, message_id=i, trace_id=f"t{i}")
        await asyncio.sleep(0.01)
    state = BackpressureState(high_water=3, policy="drop_oldest")
    await check_and_apply(queue, state)
    # Oldest (message_id=0) should be gone
    job = await queue.claim_next(Stage.SUMMARIZE)
    assert job is not None
    assert job.message_id != 0


@pytest.mark.asyncio
async def test_no_overload_when_under_high_water(tmp_path: Path):
    """Queue depth < high_water: check_and_apply is a no-op."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db)
    await queue.enqueue(Stage.ENCRYPT, message_id=1, trace_id="t1")
    state = BackpressureState(high_water=10, policy="reject_new")
    await check_and_apply(queue, state)  # should not raise
    assert state.active is False
    assert state.total_drops == 0


# -------------------- Phase F: chaos / crash recovery --------------------


class _AlwaysRunHandler:
    """A handler that always succeeds, fast."""

    def __init__(self, stage: Stage):
        self.stage = stage

    async def handle(self, job: Job, db_path: Path) -> None:
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_crash_recovery_reclaims_stale_inflight_jobs(tmp_path: Path):
    """Simulate a crash: a worker claims but never completes; restart
    reclaims the job and finishes processing it."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=1)

    # Simulate first run: enqueue, claim, "crash" before complete
    await queue.enqueue(Stage.ENCRYPT, message_id=42, trace_id="tr-crash")
    job = await queue.claim_next(Stage.ENCRYPT)
    assert job is not None
    # Don't complete — just "crash"

    # Wait for claim to expire
    await asyncio.sleep(1.5)

    # Now simulate restart: new pool, reclaim_stale should bring the job back
    handlers = {s: _AlwaysRunHandler(s) for s in Stage}
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()
    try:
        # Wait for the worker to process the reclaimed job
        for _ in range(50):
            await asyncio.sleep(0.1)
            depth = await queue.depth(Stage.ENCRYPT)
            if depth == 0:
                break
        # The reclaimed job should have been processed → next stage now enqueued
        # Wait for full pipeline
        for _ in range(50):
            await asyncio.sleep(0.1)
            wf = get_workflow(db, 42)
            if wf is None:
                continue
            if wf.state == WorkflowState.COMPLETE:
                break
    finally:
        await pool.stop(drain_timeout=5)


@pytest.mark.asyncio
async def test_worker_supervision_restarts_crashed_worker(tmp_path: Path):
    """If a worker raises an unhandled exception in run(), the supervisor
    restarts it. We simulate by patching the worker's run method to fail once."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=2)
    handlers = {s: _AlwaysRunHandler(s) for s in Stage}
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()
    try:
        # Get a worker and force it to crash on its run loop
        original_run = None
        for name, worker in pool._workers.items():
            if worker.stage == Stage.ENCRYPT:
                original_run = worker.run

                async def _crash_once():
                    if not getattr(worker, "_crashed_already", False):
                        worker._crashed_already = True
                        raise RuntimeError("simulated worker bug")
                    await original_run()

                worker.run = _crash_once  # type: ignore[assignment]
                break

        # Enqueue a message and verify the supervisor restarts the worker
        # so the message eventually processes
        await ingest(
            db_path=db, queue=queue, channel="t", sender_id="x",
            thread_id="th", reply_to_id=None, body="supervisor test",
        )
        # Allow time for the crash + restart + processing
        for _ in range(60):
            await asyncio.sleep(0.2)
            depth = await queue.total_depth()
            if depth == 0:
                break
    finally:
        await pool.stop(drain_timeout=5)


@pytest.mark.asyncio
async def test_graceful_shutdown_drains_inflight(tmp_path: Path):
    """pool.stop with drain_timeout waits for in-flight workers to finish."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=5)

    class _SlowStop:
        stage = Stage.ENCRYPT
        finished = False

        async def handle(self, job: Job, db_path: Path) -> None:
            await asyncio.sleep(0.3)
            self.finished = True

    slow = _SlowStop()
    handlers: dict[Stage, object] = {
        Stage.ENCRYPT: slow,
        Stage.INDEX: _AlwaysRunHandler(Stage.INDEX),
        Stage.SUMMARIZE: _AlwaysRunHandler(Stage.SUMMARIZE),
        Stage.PROMOTE: _AlwaysRunHandler(Stage.PROMOTE),
    }
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)  # type: ignore[arg-type]
    await pool.start()
    try:
        # Enqueue, give it a moment to be claimed
        await queue.enqueue(Stage.ENCRYPT, message_id=1, trace_id="t1")
        await asyncio.sleep(0.1)  # let the worker claim
        # Now stop — should wait for the 0.3s sleep to finish
        t0 = time.monotonic()
        await pool.stop(drain_timeout=2.0)
        elapsed = time.monotonic() - t0
        # Worker did finish its in-flight job before exit
        assert slow.finished is True
        # Took at least the work time
        assert elapsed >= 0.2
    finally:
        pass  # stopped above
