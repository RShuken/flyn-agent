"""FastAPI routes for conv-tier 2.0.

Adds shadow-mode `/api/memory/v2/*` endpoints alongside v1. The openclaw
plugin can POST to either; in shadow mode it POSTs to both and we
compare outputs after the soak.

Routes:
- POST /api/memory/v2/ingest      — accept a conv message, queue stages
- GET  /api/memory/conv/health    — health snapshot
- GET  /api/memory/conv/metrics   — Prometheus text format

The owner_id for the v2 pipeline is derived from a configurable
principals.json (shared with v1) — for slice 1 just "ryan".
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from .backpressure import (
    BackpressureState,
    OverloadError,
    check_and_apply,
)
from .handlers import (
    EncryptHandler,
    IndexHandler,
    PromoteHandler,
    SummarizeHandler,
)
from .health import build_health_snapshot, build_prometheus_metrics
from .ingest import IngestResult, ingest as conv2_ingest
from .schema import migrate
from .state import Stage
from .supervisor import WorkerPool
from .work_queue import WorkQueue

logger = logging.getLogger(__name__)


class _IngestRequest(BaseModel):
    channel: str = Field(..., min_length=1, max_length=64)
    sender_id: str = Field(..., min_length=1, max_length=128)
    thread_id: str | None = None
    reply_to_id: int | None = None
    body: str = Field(..., min_length=1, max_length=8000)
    attachments: list | None = None
    owner_id: str = Field("ryan", description="Owner whose DB to write to")


class _IngestResponse(BaseModel):
    message_id: int
    trace_id: str
    accepted: bool


class Conv2Service:
    """Holds the v2 pipeline state (DB paths, queues, worker pools per owner).

    For slice 1 we support a single owner. Multi-owner is straightforward
    additive work (one queue + pool per owner) — out of scope here.
    """

    def __init__(self, conv2_root: Path, owner_id: str = "ryan"):
        self.conv2_root = conv2_root
        self.owner_id = owner_id
        self.db_path = conv2_root / f"{owner_id}.db"
        self.queue: WorkQueue | None = None
        self.pool: WorkerPool | None = None
        self.backpressure = BackpressureState()

    async def start(self) -> None:
        self.conv2_root.mkdir(parents=True, exist_ok=True)
        migrate(self.db_path)
        self.queue = WorkQueue(db_path=self.db_path)
        handlers = {
            Stage.ENCRYPT: EncryptHandler(owner_id=self.owner_id),
            Stage.INDEX: IndexHandler(),
            Stage.SUMMARIZE: SummarizeHandler(),
            Stage.PROMOTE: PromoteHandler(owner_id=self.owner_id),
        }
        self.pool = WorkerPool(
            handlers=handlers, queue=self.queue, db_path=self.db_path
        )
        await self.pool.start()
        logger.info("conv2.service.started owner_id=%s db=%s",
                    self.owner_id, self.db_path)

    async def stop(self) -> None:
        if self.pool is not None:
            await self.pool.stop(drain_timeout=30)


def mount_conv2_routes(app: FastAPI, service: Conv2Service) -> None:
    """Add conv2 routes to an existing FastAPI app.

    Idempotent: calling twice with the same app+service is safe (FastAPI
    routes are name-keyed)."""

    router = APIRouter()

    @router.post("/api/memory/v2/ingest", response_model=_IngestResponse)
    async def v2_ingest(req: _IngestRequest) -> _IngestResponse:
        if service.queue is None:
            raise HTTPException(503, "conv2 service not started")

        # Backpressure check
        try:
            await check_and_apply(service.queue, service.backpressure)
        except OverloadError as exc:
            raise HTTPException(503, f"conv2 overload: {exc}")

        # Ingest
        result: IngestResult = await conv2_ingest(
            db_path=service.db_path,
            queue=service.queue,
            channel=req.channel,
            sender_id=req.sender_id,
            thread_id=req.thread_id,
            reply_to_id=req.reply_to_id,
            body=req.body,
            attachments=req.attachments,
        )
        return _IngestResponse(
            message_id=result.message_id,
            trace_id=result.trace_id,
            accepted=result.accepted,
        )

    @router.get("/api/memory/conv/health")
    async def health() -> dict[str, Any]:
        if service.pool is None or service.queue is None:
            return {"status": "not_started"}
        snap = await build_health_snapshot(
            service.pool, service.queue, service.db_path,
            overload_active=service.backpressure.active,
            overload_policy=service.backpressure.policy,
            last_drop_at=service.backpressure.last_drop_at,
        )
        return snap

    @router.get("/api/memory/conv/metrics")
    async def metrics() -> Response:
        if service.pool is None:
            return Response("# conv2 not started\n", media_type="text/plain")
        return Response(
            build_prometheus_metrics(service.pool),
            media_type="text/plain; version=0.0.4",
        )

    app.include_router(router)
    logger.info("conv2 routes mounted: /api/memory/v2/ingest, "
                "/api/memory/conv/health, /api/memory/conv/metrics")
