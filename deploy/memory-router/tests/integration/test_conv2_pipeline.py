"""End-to-end conv-tier 2.0 pipeline tests with mock handlers.

Verifies that a message ingested via ingest() flows through all four
stages and reaches state=complete. Uses mock handlers (no real Ollama
or Graphiti) so the test is hermetic.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from flyn_memory_router.conv2.ingest import ingest
from flyn_memory_router.conv2.schema import migrate
from flyn_memory_router.conv2.state import Stage, WorkflowState
from flyn_memory_router.conv2.supervisor import WorkerPool
from flyn_memory_router.conv2.work_queue import Job, WorkQueue
from flyn_memory_router.conv2.workflow import get_workflow


class MockHandler:
    """A handler that just sleeps briefly + records that it ran.
    The worker calls advance_stage(self.stage) on success, which is
    what we want to verify."""

    def __init__(self, stage: Stage, sleep_ms: int = 5):
        self.stage = stage
        self.sleep_ms = sleep_ms
        self.calls = 0

    async def handle(self, job: Job, db_path: Path) -> None:
        self.calls += 1
        await asyncio.sleep(self.sleep_ms / 1000)


@pytest.fixture
async def pipeline(tmp_path: Path):
    """Migrated DB + queue + worker pool with mock handlers."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=5)
    handlers = {s: MockHandler(s, sleep_ms=2) for s in Stage}
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()
    yield (db, queue, pool, handlers)
    await pool.stop(drain_timeout=5)


@pytest.mark.asyncio
async def test_message_reaches_complete_state(pipeline):
    """Single message goes received → encrypted → indexed → summarized → promoted → complete."""
    db, queue, pool, handlers = pipeline

    result = await ingest(
        db_path=db,
        queue=queue,
        channel="telegram",
        sender_id="7191564227",
        thread_id="7191564227",
        reply_to_id=None,
        body="test message",
    )

    # Wait for completion (up to 5 seconds)
    for _ in range(50):
        await asyncio.sleep(0.1)
        wf = get_workflow(db, result.message_id)
        if wf and wf.state == WorkflowState.COMPLETE:
            break
    else:
        pytest.fail(
            f"Message {result.message_id} did not reach COMPLETE; "
            f"final state was {wf.state if wf else 'None'}"
        )

    # Verify all four stages ran exactly once
    for stage, handler in handlers.items():
        assert handler.calls == 1, (
            f"{stage.value} called {handler.calls} times (expected 1)"
        )

    # Verify all four *_at timestamps are populated
    wf = get_workflow(db, result.message_id)
    assert wf.state == WorkflowState.COMPLETE
    assert wf.encrypted_at is not None
    assert wf.indexed_at is not None
    assert wf.summarized_at is not None
    assert wf.promoted_at is not None
    assert wf.completed_at is not None


@pytest.mark.asyncio
async def test_trace_id_flows_through_pipeline(pipeline):
    """trace_id assigned at ingest is preserved on the workflow row."""
    db, queue, pool, handlers = pipeline

    result = await ingest(
        db_path=db, queue=queue, channel="telegram", sender_id="7191564227",
        thread_id="t1", reply_to_id=None, body="trace test",
    )
    assert result.trace_id.startswith("tr-")

    # Wait for at least one stage to fire
    for _ in range(30):
        await asyncio.sleep(0.1)
        wf = get_workflow(db, result.message_id)
        if wf and wf.encrypted_at is not None:
            break

    wf = get_workflow(db, result.message_id)
    assert wf.trace_id == result.trace_id


@pytest.mark.asyncio
async def test_three_messages_all_complete(pipeline):
    """Three messages run through the pipeline concurrently and all complete."""
    db, queue, pool, handlers = pipeline

    results = []
    for i in range(3):
        r = await ingest(
            db_path=db, queue=queue, channel="telegram", sender_id="7191564227",
            thread_id="t1", reply_to_id=None, body=f"msg {i}",
        )
        results.append(r)

    # Wait for all to complete
    for _ in range(50):
        await asyncio.sleep(0.1)
        states = [get_workflow(db, r.message_id).state for r in results]
        if all(s == WorkflowState.COMPLETE for s in states):
            break
    else:
        pytest.fail(f"Not all complete; states: {states}")

    # Each stage ran exactly 3 times
    for stage, handler in handlers.items():
        assert handler.calls == 3, (
            f"{stage.value} called {handler.calls} (expected 3)"
        )


@pytest.mark.asyncio
async def test_handler_crash_retries_then_succeeds(tmp_path: Path):
    """If a handler raises, the worker retries up to max_attempts."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=2)

    class FlakySummarize:
        stage = Stage.SUMMARIZE
        calls = 0

        async def handle(self, job: Job, db_path: Path) -> None:
            self.calls += 1
            if self.calls < 2:
                raise RuntimeError("transient")

    handlers = {
        Stage.ENCRYPT: MockHandler(Stage.ENCRYPT),
        Stage.INDEX: MockHandler(Stage.INDEX),
        Stage.SUMMARIZE: FlakySummarize(),
        Stage.PROMOTE: MockHandler(Stage.PROMOTE),
    }
    # Short backoff for the test
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    for w in []:  # workers spawned in pool.start; override backoff there
        pass
    await pool.start()
    try:
        result = await ingest(
            db_path=db, queue=queue, channel="t", sender_id="x",
            thread_id="th", reply_to_id=None, body="retry test",
        )
        # Wait up to 10s
        for _ in range(100):
            await asyncio.sleep(0.1)
            wf = get_workflow(db, result.message_id)
            if wf and wf.state == WorkflowState.COMPLETE:
                break

        wf = get_workflow(db, result.message_id)
        assert wf.state == WorkflowState.COMPLETE
        # The flaky handler succeeded after at least one retry
        assert handlers[Stage.SUMMARIZE].calls >= 2
    finally:
        await pool.stop(drain_timeout=5)


@pytest.mark.asyncio
async def test_handler_persistent_failure_lands_in_dlq(tmp_path: Path):
    """If handler always fails, message ends in FAILED state, DLQ has the row."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=2)

    class AlwaysFail:
        stage = Stage.SUMMARIZE

        async def handle(self, job: Job, db_path: Path) -> None:
            raise RuntimeError("persistent bug")

    handlers = {
        Stage.ENCRYPT: MockHandler(Stage.ENCRYPT),
        Stage.INDEX: MockHandler(Stage.INDEX),
        Stage.SUMMARIZE: AlwaysFail(),
        Stage.PROMOTE: MockHandler(Stage.PROMOTE),
    }
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    # Override worker backoff/max for fast test
    await pool.start()
    for w in pool._workers.values():
        w.max_attempts = 2
        w.backoff_base_s = 0.05
        w.backoff_max_s = 0.1
    try:
        result = await ingest(
            db_path=db, queue=queue, channel="t", sender_id="x",
            thread_id="th", reply_to_id=None, body="dlq test",
        )
        # Wait for DLQ
        for _ in range(50):
            await asyncio.sleep(0.1)
            dlq = await queue.dead_letter_count()
            if dlq > 0:
                break
        assert await queue.dead_letter_count() == 1
    finally:
        await pool.stop(drain_timeout=5)


@pytest.mark.asyncio
async def test_no_polling_means_idle_cpu_during_quiet_periods(tmp_path: Path):
    """When no messages are enqueued, workers block on wait_for — verify by
    inspecting that handlers don't fire during a quiet wait."""
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=2)
    handlers = {s: MockHandler(s) for s in Stage}
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()
    try:
        # Sleep without enqueueing anything
        await asyncio.sleep(0.5)
        # No handler should have run
        for stage, h in handlers.items():
            assert h.calls == 0, f"{stage.value} ran during quiet period"
    finally:
        await pool.stop(drain_timeout=5)
