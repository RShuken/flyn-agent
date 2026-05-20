"""Generic async worker loop + handler protocol for conv-tier 2.0.

Each pipeline stage (encrypt, index, summarize, promote) has its own
StageHandler implementation. The Worker class takes a stage + handler
and runs the canonical loop:

  while not stopping:
    job = await queue.claim_next(stage)
    if job is None:
      await queue.wait_for(stage, timeout=heartbeat_seconds)
      continue
    try:
      await handler.handle(job)
      await queue.complete(job)
      <advance workflow state + enqueue next stages>
    except Exception as exc:
      moved_to_dlq = await queue.fail(job, str(exc), ...)
      <record failure on workflow; if dlq, mark workflow FAILED>

The loop never polls; it blocks on the asyncio.Event in queue.wait_for.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from .state import ALLOWED_TRANSITIONS, Stage, WorkflowState
from .work_queue import Job, WorkQueue
from .workflow import advance_stage, get_workflow, record_failure

logger = logging.getLogger(__name__)

# Heartbeat: how often the worker wakes up even if the queue is silent,
# so we can check stop flags and re-claim stale jobs.
DEFAULT_HEARTBEAT_S = 5.0


# Linear pipeline: each stage's success enqueues the next stage.
# encrypt → index → summarize → promote → (complete).
_NEXT_STAGE: dict[Stage, Stage | None] = {
    Stage.ENCRYPT: Stage.INDEX,
    Stage.INDEX: Stage.SUMMARIZE,
    Stage.SUMMARIZE: Stage.PROMOTE,
    Stage.PROMOTE: None,
}


class StageHandler(Protocol):
    """The interface each stage handler implements."""

    stage: Stage

    async def handle(self, job: Job, db_path: Path) -> None:
        """Do the stage-specific work. Raise on failure (caller retries).

        On success, the worker will call advance_stage to update the
        workflow row's state + timestamp; the handler does NOT touch
        the workflow row itself, only the messages table or external
        services.
        """
        ...


class Worker:
    """One worker instance for one stage. Spawn N for higher concurrency."""

    def __init__(
        self,
        handler: StageHandler,
        queue: WorkQueue,
        db_path: Path,
        worker_idx: int = 0,
        max_attempts: int = 3,
        backoff_base_s: float = 2.0,
        backoff_max_s: float = 60.0,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
        metrics: "Metrics | None" = None,
    ):
        self.handler = handler
        self.stage = handler.stage
        self.queue = queue
        self.db_path = db_path
        self.worker_idx = worker_idx
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.heartbeat_s = heartbeat_s
        self.metrics = metrics
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True
        # Wake the worker if blocked on wait_for
        self.queue.notify(self.stage)

    @property
    def name(self) -> str:
        return f"{self.stage.value}#{self.worker_idx}"

    async def run(self) -> None:
        """Main loop. Runs until stop() is called."""
        logger.info("worker.start", extra={"worker": self.name})
        while not self._stopping:
            job = await self.queue.claim_next(self.stage)
            if job is None:
                await self.queue.wait_for(self.stage, timeout=self.heartbeat_s)
                continue
            await self._process(job)
        logger.info("worker.stop", extra={"worker": self.name})

    async def _process(self, job: Job) -> None:
        """Handle one job: call handler, advance state on success, retry on fail."""
        t0 = time.monotonic()
        log_ctx = {
            "stage": self.stage.value,
            "message_id": job.message_id,
            "trace_id": job.trace_id,
            "attempt": job.attempts,
            "worker": self.name,
        }
        try:
            await self.handler.handle(job, self.db_path)
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Advance workflow state + queue cleanup atomic-ish via two ops
            await asyncio.to_thread(
                advance_stage, self.db_path, job.message_id, self.stage
            )
            await self.queue.complete(job)

            # Enqueue the next stage in the linear pipeline
            next_stage = _NEXT_STAGE[self.stage]
            if next_stage is not None:
                await self.queue.enqueue(
                    next_stage, job.message_id, job.trace_id
                )

            logger.info("worker.success", extra={
                **log_ctx, "duration_ms": duration_ms, "outcome": "success",
            })
            if self.metrics:
                self.metrics.record_stage_latency(self.stage, duration_ms, "success")

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_text = f"{type(exc).__name__}: {exc}"

            # Record failure on workflow
            workflow_state = await asyncio.to_thread(
                record_failure,
                self.db_path,
                job.message_id,
                self.stage,
                error_text,
                self.max_attempts,
            )

            # Re-enqueue with backoff (or move to DLQ)
            backoff = min(
                self.backoff_base_s * (2 ** (job.attempts - 1)),
                self.backoff_max_s,
            )
            moved_to_dlq = await self.queue.fail(
                job, error_text, int(backoff), self.max_attempts
            )

            logger.warning("worker.failure", extra={
                **log_ctx, "duration_ms": duration_ms,
                "error": error_text,
                "moved_to_dlq": moved_to_dlq,
                "workflow_state": workflow_state.value,
                "outcome": "dlq" if moved_to_dlq else "retry",
            })
            if self.metrics:
                outcome = "dlq" if moved_to_dlq else "retry"
                self.metrics.record_stage_latency(self.stage, duration_ms, outcome)


class Metrics:
    """In-memory metrics. Replaceable with a Prometheus client later.

    Records per-stage latency histograms + outcome counters. The /health
    endpoint reads p50/p99 from this.
    """

    def __init__(self):
        self._samples: dict[Stage, list[int]] = {s: [] for s in Stage}
        self._counts: dict[tuple[Stage, str], int] = {}
        self._max_samples = 1000  # rolling window per stage

    def record_stage_latency(self, stage: Stage, duration_ms: int, outcome: str) -> None:
        samples = self._samples[stage]
        samples.append(duration_ms)
        if len(samples) > self._max_samples:
            del samples[0]
        key = (stage, outcome)
        self._counts[key] = self._counts.get(key, 0) + 1

    def percentile(self, stage: Stage, p: float) -> int:
        """Return the pNN latency in ms for `stage`, or 0 if no samples."""
        samples = sorted(self._samples[stage])
        if not samples:
            return 0
        k = int(len(samples) * p / 100)
        k = min(k, len(samples) - 1)
        return samples[k]

    def count(self, stage: Stage, outcome: str) -> int:
        return self._counts.get((stage, outcome), 0)
