"""Worker pool supervision for conv-tier 2.0.

Spawns N worker coroutines per stage (configurable). If a worker
crashes with an unhandled exception, the supervisor logs, emits a
crash metric, and restarts it after a brief backoff. Pool size stays
constant.

Graceful shutdown: signals all workers to stop, drains in-flight
work (up to a timeout), then exits cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .state import Stage
from .worker import Metrics, StageHandler, Worker
from .work_queue import WorkQueue

logger = logging.getLogger(__name__)


class WorkerPool:
    """Manages N workers per stage with crash recovery."""

    def __init__(
        self,
        handlers: dict[Stage, StageHandler],
        queue: WorkQueue,
        db_path: Path,
        concurrency: dict[Stage, int] | None = None,
        metrics: Metrics | None = None,
    ):
        self.handlers = handlers
        self.queue = queue
        self.db_path = db_path
        # Default concurrency: 1 per stage. Override per-stage via the
        # `concurrency` dict (e.g., {Stage.SUMMARIZE: 2} for parallel Ollama calls).
        self.concurrency = concurrency or {s: 1 for s in Stage}
        self.metrics = metrics or Metrics()
        self._workers: dict[str, Worker] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = False

    @property
    def workers_alive(self) -> dict[str, int]:
        """Per-stage count of running workers, used by /health."""
        alive: dict[str, int] = {s.value: 0 for s in Stage}
        for name, task in self._tasks.items():
            if not task.done():
                stage_name = name.split("#")[0]
                alive[stage_name] = alive.get(stage_name, 0) + 1
        return alive

    async def start(self) -> None:
        """Spawn all workers. Re-claims stale jobs from the queue first."""
        # Crash recovery: reclaim any stale claims left over from previous run
        await self.queue.reclaim_stale()

        # Spawn worker tasks
        for stage, n in self.concurrency.items():
            handler = self.handlers.get(stage)
            if handler is None:
                logger.warning("No handler for stage %s — skipping", stage.value)
                continue
            for i in range(n):
                w = Worker(
                    handler=handler,
                    queue=self.queue,
                    db_path=self.db_path,
                    worker_idx=i,
                    metrics=self.metrics,
                )
                self._workers[w.name] = w
                self._tasks[w.name] = asyncio.create_task(
                    self._supervise(w), name=f"conv2-worker-{w.name}"
                )
        logger.info("WorkerPool started with %d workers", len(self._tasks))

    async def _supervise(self, worker: Worker) -> None:
        """Run a worker; restart on unhandled crash until stopping."""
        while not self._stopping:
            try:
                await worker.run()
            except Exception as exc:
                logger.exception(
                    "worker.crash worker=%s error=%s", worker.name, exc
                )
                # Brief backoff before restart so we don't spin on a permanent bug
                await asyncio.sleep(1.0)
                continue
            # run() returned cleanly (stop was called)
            break

    async def stop(self, drain_timeout: float = 30.0) -> None:
        """Graceful shutdown: stop workers, wait for in-flight to finish."""
        logger.info("WorkerPool stopping (drain timeout=%ss)", drain_timeout)
        self._stopping = True
        # Signal each worker to stop
        for w in self._workers.values():
            w.stop()
        # Wait for tasks to finish (with timeout)
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks.values(), return_exceptions=True),
                    timeout=drain_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("WorkerPool drain timed out; forcing exit")
                for task in self._tasks.values():
                    if not task.done():
                        task.cancel()
        logger.info("WorkerPool stopped")
