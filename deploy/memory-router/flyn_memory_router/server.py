"""FastAPI app + route handlers for the MemoryRouter.

Public interface:
    POST   /api/memory/ingest           (body: InboundEvent)            -> EventResult
    POST   /api/memory/pin              (body: PinRequest)              -> {ok: true}
    DELETE /api/memory/pin/<subject>    (query: sender_role)            -> {ok: true}
    GET    /api/health                                                  -> {ok: true}

This file is the routing layer only. Business logic lives in router.py / pin.py / adapters/.
"""
from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .adapters import AdapterRegistry
from .adapters.cold import ColdCapturesIndexAdapter
from .adapters.cool import CoolDailyRollupAdapter
from .adapters.hot import HotMemoryMdAdapter
from .adapters.lesson import LessonKnowledgeAdapter
from .adapters.warm import WarmGraphitiAdapter, WarmWorkspaceFileAdapter
from .config import Config
from .dedup import DedupStore
from .pin import PinRequest, pin_permanent, unpin
from .router import Router
from .types import EventResult, InboundEvent, Tier


class _PinBody(BaseModel):
    subject: str
    body: str
    sender_role: Literal["owner", "teammate", "other"]


class _MaintBody(BaseModel):
    sender_role: Literal["owner", "teammate", "other"]


def build_app(http_client: Any | None = None) -> FastAPI:
    cfg = Config.from_env()
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.workspace.mkdir(parents=True, exist_ok=True)
    cfg.workspace_memory_dir.mkdir(parents=True, exist_ok=True)
    cfg.knowledge_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the DB directory exists
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)

    dedup = DedupStore(db_path=cfg.db_path)
    registry = AdapterRegistry()

    hot = HotMemoryMdAdapter(memory_md=cfg.memory_md)
    warm_ws = WarmWorkspaceFileAdapter(memory_dir=cfg.workspace_memory_dir)
    warm_gr = WarmGraphitiAdapter(
        graphiti_url=cfg.graphiti_url,
        http=http_client or httpx.Client(timeout=httpx.Timeout(180.0)),
    )
    cool = CoolDailyRollupAdapter(memory_dir=cfg.workspace_memory_dir)
    cold = ColdCapturesIndexAdapter(index_path=cfg.home / "captures_index.jsonl")
    lesson = LessonKnowledgeAdapter(knowledge_dir=cfg.knowledge_dir)

    # Phase 0 fanout: hot events ONLY land in MEMORY.md. Per spec §2.5, hot also fans out to
    # Graphiti + workspace/memory + captures index. Wire warm_gr, warm_ws, and cold under
    # Tier.HOT in Phase 1.
    registry.register(Tier.HOT, hot)
    registry.register(Tier.WARM, warm_ws)
    registry.register(Tier.WARM, warm_gr)
    registry.register(Tier.COOL, cool)
    registry.register(Tier.COLD, cold)
    # Phase 0 fanout: lesson events ONLY land in KNOWLEDGE/. Per spec §2.5, lesson also posts
    # a Graphiti episode (event_type "lesson-learned"). Wire warm_gr under Tier.LESSON in Phase 1.
    registry.register(Tier.LESSON, lesson)

    router = Router(registry=registry, dedup=dedup)

    app = FastAPI(title="flyn-memory-router", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "flyn-memory-router", "port": cfg.port}

    @app.post("/api/memory/ingest", response_model=EventResult)
    def ingest(event: InboundEvent) -> EventResult:
        return router.ingest(event)

    @app.post("/api/memory/pin")
    def pin(req: _PinBody) -> dict[str, bool]:
        try:
            pin_permanent(hot, PinRequest(subject=req.subject, body=req.body,
                                          sender_role=req.sender_role))
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        return {"ok": True}

    @app.delete("/api/memory/pin/{subject}")
    def unpin_route(subject: str,
                    sender_role: Literal["owner", "teammate", "other"] = Query(...)) -> dict[str, Any]:
        try:
            existed = unpin(hot, subject, sender_role=sender_role)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        return {"ok": True, "existed": existed}

    @app.post("/api/memory/maintenance/decay")
    def decay_route(req: _MaintBody) -> dict[str, Any]:
        if req.sender_role != "owner":
            raise HTTPException(status_code=403, detail="owner only")
        removed = hot.decay()
        return {"ok": True, "removed": removed}

    return app
