"""Health + metrics endpoints for conv-tier 2.0.

`build_health_snapshot()` returns the GET /api/memory/conv/health body
per the design doc: per-stage queue depths, latency percentiles, stuck
count, dead-letter count, worker alive count, overload state.

`build_prometheus_metrics()` returns Prometheus text format for the
GET /api/memory/conv/metrics endpoint.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .state import Stage
from .supervisor import WorkerPool
from .work_queue import WorkQueue
from .workflow import find_stuck


async def build_health_snapshot(
    pool: WorkerPool,
    queue: WorkQueue,
    db_path: Path,
    stuck_threshold_s: int = 60,
    overload_active: bool = False,
    overload_policy: str = "reject_new",
    last_drop_at: str | None = None,
) -> dict[str, Any]:
    """Build the /api/memory/conv/health JSON body.

    Reads queue depths, latency percentiles from the supervisor's Metrics,
    counts stuck rows via a single SQL query, and reports worker liveness.
    """
    # Queue depths per stage
    depths = {s.value: await queue.depth(s) for s in Stage}

    # Latency p50/p99 per stage from the supervisor's metrics
    latency_ms: dict[str, dict[str, int]] = {}
    for s in Stage:
        latency_ms[s.value] = {
            "p50": pool.metrics.percentile(s, 50),
            "p99": pool.metrics.percentile(s, 99),
        }
    # End-to-end aggregate: sum of stage p50/p99 (approximation; precise
    # would need per-message tracing aggregation)
    e2e_p50 = sum(latency_ms[s.value]["p50"] for s in Stage)
    e2e_p99 = sum(latency_ms[s.value]["p99"] for s in Stage)
    latency_ms["end_to_end"] = {"p50": e2e_p50, "p99": e2e_p99}

    # Stuck count via the workflow query
    stuck_rows = await asyncio.to_thread(find_stuck, db_path, stuck_threshold_s)
    stuck_by_stage: dict[str, int] = {s.value: 0 for s in Stage}
    for row in stuck_rows:
        # Determine "next stage to run" from current state
        # received → encrypt, encrypted → index, etc.
        from .state import WorkflowState
        next_stage_map = {
            WorkflowState.RECEIVED: "encrypt",
            WorkflowState.ENCRYPTED: "index",
            WorkflowState.INDEXED: "summarize",
            WorkflowState.SUMMARIZED: "promote",
            WorkflowState.PROMOTED: "promote",  # waiting on completion check
        }
        bucket = next_stage_map.get(row.state, "encrypt")
        stuck_by_stage[bucket] = stuck_by_stage.get(bucket, 0) + 1

    # Dead-letter count
    dlq_count = await queue.dead_letter_count()

    # Workers alive (pool reports per-stage)
    workers_alive = pool.workers_alive

    return {
        "queue_depths": depths,
        "latency_ms": latency_ms,
        "stuck": stuck_by_stage,
        "dead_letter_count": dlq_count,
        "workers_alive": workers_alive,
        "overload": {
            "active": overload_active,
            "policy": overload_policy,
            "last_drop_at": last_drop_at,
        },
    }


def build_prometheus_metrics(pool: WorkerPool) -> str:
    """Return Prometheus text format with per-stage histograms + counters."""
    lines: list[str] = []

    # Latency histogram-style (we use buckets manually)
    lines.append("# HELP conv2_stage_latency_ms_p50 p50 latency per pipeline stage")
    lines.append("# TYPE conv2_stage_latency_ms_p50 gauge")
    for s in Stage:
        lines.append(f'conv2_stage_latency_ms_p50{{stage="{s.value}"}} '
                     f'{pool.metrics.percentile(s, 50)}')

    lines.append("# HELP conv2_stage_latency_ms_p99 p99 latency per pipeline stage")
    lines.append("# TYPE conv2_stage_latency_ms_p99 gauge")
    for s in Stage:
        lines.append(f'conv2_stage_latency_ms_p99{{stage="{s.value}"}} '
                     f'{pool.metrics.percentile(s, 99)}')

    # Outcome counters
    lines.append("# HELP conv2_stage_outcome_total Per-stage outcome counter")
    lines.append("# TYPE conv2_stage_outcome_total counter")
    for s in Stage:
        for outcome in ("success", "retry", "dlq"):
            count = pool.metrics.count(s, outcome)
            lines.append(
                f'conv2_stage_outcome_total{{stage="{s.value}",outcome="{outcome}"}} '
                f'{count}'
            )

    # Workers alive
    lines.append("# HELP conv2_workers_alive Number of running workers per stage")
    lines.append("# TYPE conv2_workers_alive gauge")
    for stage_name, count in pool.workers_alive.items():
        lines.append(f'conv2_workers_alive{{stage="{stage_name}"}} {count}')

    return "\n".join(lines) + "\n"
