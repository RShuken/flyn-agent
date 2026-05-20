"""Observability: health endpoint, Prometheus metrics, structured logs."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from flyn_memory_router.conv2.health import (
    build_health_snapshot,
    build_prometheus_metrics,
)
from flyn_memory_router.conv2.ingest import ingest
from flyn_memory_router.conv2.logging_setup import StructuredFormatter
from flyn_memory_router.conv2.schema import migrate
from flyn_memory_router.conv2.state import Stage, WorkflowState
from flyn_memory_router.conv2.supervisor import WorkerPool
from flyn_memory_router.conv2.work_queue import Job, WorkQueue
from flyn_memory_router.conv2.workflow import create_workflow, get_workflow


class _NoopHandler:
    def __init__(self, stage: Stage):
        self.stage = stage

    async def handle(self, job: Job, db_path: Path) -> None:
        await asyncio.sleep(0.001)


@pytest.fixture
async def pool_fixture(tmp_path: Path):
    db = tmp_path / "owner.db"
    migrate(db)
    queue = WorkQueue(db_path=db, claim_timeout_s=5)
    handlers = {s: _NoopHandler(s) for s in Stage}
    pool = WorkerPool(handlers=handlers, queue=queue, db_path=db)
    await pool.start()
    yield db, queue, pool
    await pool.stop(drain_timeout=5)


@pytest.mark.asyncio
async def test_health_snapshot_structure(pool_fixture):
    """Health snapshot has the canonical JSON shape from the design doc."""
    db, queue, pool = pool_fixture
    snap = await build_health_snapshot(pool, queue, db)
    assert set(snap.keys()) >= {
        "queue_depths", "latency_ms", "stuck",
        "dead_letter_count", "workers_alive", "overload",
    }
    assert set(snap["queue_depths"].keys()) == {"encrypt", "index", "summarize", "promote"}
    assert set(snap["latency_ms"].keys()) == {"encrypt", "index", "summarize", "promote", "end_to_end"}
    assert all("p50" in v and "p99" in v for v in snap["latency_ms"].values())
    assert snap["overload"]["active"] is False


@pytest.mark.asyncio
async def test_health_stuck_count_reflects_old_workflow_rows(pool_fixture):
    """Backdated workflow rows show up as stuck."""
    db, queue, pool = pool_fixture
    # Insert a workflow row by hand with backdated created_at
    create_workflow(db, message_id=99, trace_id="tr-stuck")
    from flyn_memory_router.conv2.schema import open_db
    with open_db(db) as conn:
        conn.execute(
            "UPDATE conversation_workflow SET created_at = datetime('now', '-300 seconds') "
            "WHERE message_id = 99"
        )
    snap = await build_health_snapshot(pool, queue, db, stuck_threshold_s=60)
    total_stuck = sum(snap["stuck"].values())
    assert total_stuck >= 1


@pytest.mark.asyncio
async def test_health_workers_alive_reports_pool(pool_fixture):
    """workers_alive reflects active worker count."""
    db, queue, pool = pool_fixture
    snap = await build_health_snapshot(pool, queue, db)
    assert sum(snap["workers_alive"].values()) >= 4  # one per stage


@pytest.mark.asyncio
async def test_prometheus_metrics_format(pool_fixture):
    """Prometheus output has HELP/TYPE/value lines per metric."""
    _, _, pool = pool_fixture
    text = build_prometheus_metrics(pool)
    assert "# HELP conv2_stage_latency_ms_p50" in text
    assert "# TYPE conv2_stage_latency_ms_p50 gauge" in text
    assert 'conv2_stage_latency_ms_p50{stage="encrypt"}' in text
    assert "conv2_stage_outcome_total" in text
    assert "conv2_workers_alive" in text


def test_structured_log_emits_trace_id_and_extras():
    """StructuredFormatter renders extras as top-level JSON fields."""
    fmt = StructuredFormatter()
    rec = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="worker.success", args=(), exc_info=None,
    )
    rec.trace_id = "tr-xyz"
    rec.message_id = 42
    rec.stage = "summarize"
    rec.duration_ms = 1234
    rec.outcome = "success"
    out = fmt.format(rec)
    data = json.loads(out)
    assert data["trace_id"] == "tr-xyz"
    assert data["message_id"] == 42
    assert data["stage"] == "summarize"
    assert data["duration_ms"] == 1234
    assert data["outcome"] == "success"
    assert data["message"] == "worker.success"
    assert data["level"] == "info"


def test_structured_log_handles_records_without_extras():
    """A plain log record without extras still produces valid JSON."""
    fmt = StructuredFormatter()
    rec = logging.LogRecord(
        name="test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="boot complete", args=(), exc_info=None,
    )
    out = fmt.format(rec)
    data = json.loads(out)
    assert data["message"] == "boot complete"
    assert data["level"] == "warning"
