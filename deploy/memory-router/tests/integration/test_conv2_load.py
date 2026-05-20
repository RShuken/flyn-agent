"""Load test: verify the pipeline handles 10x normal volume + backpressure activates."""
from __future__ import annotations

import asyncio
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


class _FastHandler:
    """Realistic noop handler — fast like the trivial encrypt/index stages."""

    def __init__(self, stage: Stage, sleep_s: float = 0.001):
        self.stage = stage
        self.sleep_s = sleep_s

    async def handle(self, job: Job, db_path: Path) -> None:
        await asyncio.sleep(self.sleep_s)


@pytest.mark.asyncio
async def test_pipeline_handles_100_messages_under_30s(tmp_path: Path):
    """Throughput test: 100 messages should all reach COMPLETE within 30s
    on the default 1-worker-per-stage configuration."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=10)
    handlers = {s: _FastHandler(s) for s in Stage}
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()
    try:
        t0 = time.monotonic()
        # Enqueue all 100 messages
        results = []
        for i in range(100):
            r = await ingest(
                db_path=db, queue=queue, channel="t", sender_id="x",
                thread_id="th", reply_to_id=None, body=f"msg {i}",
            )
            results.append(r)
        t_enqueued = time.monotonic() - t0

        # Wait for all to reach COMPLETE
        for _ in range(300):  # max 30s
            await asyncio.sleep(0.1)
            completed = sum(
                1 for r in results
                if (wf := get_workflow(db, r.message_id))
                and wf.state == WorkflowState.COMPLETE
            )
            if completed == 100:
                break
        t_total = time.monotonic() - t0

        assert completed == 100, f"only {completed}/100 completed in 30s"
        # Sanity: should be well under 30s in practice
        assert t_total < 30, f"100-message throughput took {t_total:.1f}s"
        print(f"\n100 msgs enqueued in {t_enqueued:.2f}s, all complete in {t_total:.2f}s")
    finally:
        await pool.stop(drain_timeout=10)


@pytest.mark.asyncio
async def test_backpressure_activates_at_high_water(tmp_path: Path):
    """Under sustained load with throughput < input, queue grows to HIGH_WATER
    and backpressure check kicks in."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=10)

    # Slow handlers so the queue builds up
    handlers = {
        Stage.ENCRYPT: _FastHandler(Stage.ENCRYPT, sleep_s=0.2),
        Stage.INDEX: _FastHandler(Stage.INDEX, sleep_s=0.001),
        Stage.SUMMARIZE: _FastHandler(Stage.SUMMARIZE, sleep_s=0.001),
        Stage.PROMOTE: _FastHandler(Stage.PROMOTE, sleep_s=0.001),
    }
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()

    state = BackpressureState(high_water=10, policy="reject_new")
    rejected = 0
    accepted = 0
    try:
        # Try to enqueue 50 messages rapidly while the slow encrypt stage
        # holds the queue. Each ingest is gated by check_and_apply.
        for i in range(50):
            try:
                await check_and_apply(queue, state)
                await ingest(
                    db_path=db, queue=queue, channel="t", sender_id="x",
                    thread_id="th", reply_to_id=None, body=f"msg {i}",
                )
                accepted += 1
            except OverloadError:
                rejected += 1
            await asyncio.sleep(0.01)  # rapid-fire

        # At least one rejection should have happened
        assert rejected > 0, "backpressure never activated under load"
        # State reflects overload
        assert state.total_drops >= rejected
        assert state.last_drop_at is not None
        print(f"\nUnder load: accepted={accepted} rejected={rejected} "
              f"total_drops={state.total_drops}")
    finally:
        await pool.stop(drain_timeout=10)
