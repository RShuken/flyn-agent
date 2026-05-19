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
from pydantic import BaseModel, Field

from . import lint as lint_module
from . import query as query_module
from .adapters import AdapterRegistry
from .adapters.cold import ColdCapturesIndexAdapter
from .adapters.conv_read import ConvReadAdapter
from .adapters.conv_write import ConvWriteAdapter
from .adapters.cool import CoolDailyRollupAdapter
from .adapters.hot import HotMemoryMdAdapter
from .adapters.lesson import LessonKnowledgeAdapter
from .adapters.warm import WarmGraphitiAdapter, WarmWorkspaceFileAdapter
from .config import Config, READ_SOURCES
from .conv.owner import OwnerRegistry
from .conv.summarizer import SummarizerWorker
from .dedup import DedupStore
from .health_tracker import TRACKER
from .logging_contract import gc_logs
from .pin import PinRequest, pin_permanent, unpin
from .router import Router
from .types import EventResult, Hit, InboundEvent, Tier


class _PinBody(BaseModel):
    subject: str
    body: str
    sender_role: Literal["owner", "teammate", "other"]


class _MaintBody(BaseModel):
    sender_role: Literal["owner", "teammate", "other"]


class _QueryBody(BaseModel):
    q: str = Field(..., min_length=1, max_length=2000)
    include: list[str] | None = None
    exclude: list[str] | None = None
    top_k: int = Field(10, ge=1, le=100)


class _LintBody(BaseModel):
    entities: list[str] = Field(default_factory=list, max_length=100)
    sources: list[str] | None = None


def build_app(http_client: Any | None = None) -> FastAPI:
    cfg = Config.from_env()
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.workspace.mkdir(parents=True, exist_ok=True)
    cfg.workspace_memory_dir.mkdir(parents=True, exist_ok=True)
    cfg.knowledge_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the DB directory exists
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)

    # Phase 0f task A2: log retention sweep at startup
    try:
        gc_logs(cfg.log_dir)
    except Exception:
        # Never fail startup over log GC; the daily task will retry.
        pass

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

    # --- Conversation tier (Telegram slice 1) ---
    cfg.conv_root.mkdir(parents=True, exist_ok=True)
    owner_registry = OwnerRegistry(
        owners_db_path=cfg.conv_owners_db_path,
        principals_json=cfg.principals_json_path,
    )
    conv_write_adapter = ConvWriteAdapter(
        registry=owner_registry,
        conv_root=cfg.conv_root,
        queue_dir=cfg.queue_dir,
        graphiti_url=cfg.graphiti_url,
        http_client=http_client,
    )
    registry.register(Tier.CONV, conv_write_adapter)

    # Async summarizer worker — pulls jobs from queue, calls Ollama
    summarizer = SummarizerWorker(queue_dir=cfg.queue_dir)
    summarizer.start()

    router = Router(registry=registry, dedup=dedup)

    app = FastAPI(title="flyn-memory-router", version="0.1.0")

    @app.on_event("startup")
    async def _schedule_daily_gc():
        async def _loop():
            import asyncio
            while True:
                await asyncio.sleep(86400)
                try:
                    gc_logs(cfg.log_dir)
                except Exception:
                    pass
        import asyncio
        asyncio.create_task(_loop())

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "flyn-memory-router", "port": cfg.port}

    @app.post("/api/memory/ingest", response_model=EventResult)
    def ingest(event: InboundEvent) -> EventResult:
        if event.event_type == "conversation_message":
            result = conv_write_adapter.write(event)
            return EventResult(
                accepted=result.ok,
                deduped=False,
                importance=event.importance or "warm",
                tiers_written=[Tier.CONV] if result.ok else [],
                notes=[result.detail] if result.detail else [],
            )
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

    @app.post("/api/memory/query")
    async def query_route(body: _QueryBody) -> dict[str, Any]:
        result = await query_module.query(
            body.q, include=body.include, exclude=body.exclude, top_k=body.top_k
        )
        # Catastrophic: every included adapter failed
        if (result.source_errors and result.included_sources
                and len(result.source_errors) == len(result.included_sources)):
            raise HTTPException(
                status_code=502,
                detail={
                    "query_id": result.query_id,
                    "source_errors": [e.model_dump() for e in result.source_errors],
                    "included_sources": result.included_sources,
                },
            )
        return result.model_dump()

    @app.post("/api/memory/lint")
    async def lint_route(body: _LintBody) -> dict[str, Any]:
        entities = body.entities
        if not entities:
            entities = lint_module.discover_entities_from_vault(cfg.reference_vault)
        findings = []
        for entity in entities:
            result = await query_module.query(entity, include=body.sources, top_k=3)
            per_source: dict[str, list[Hit]] = {}
            for h in result.hits:
                per_source.setdefault(h.source, []).append(h)
            ent_findings = await lint_module.detect_drift(entity, per_source)
            findings.extend(ent_findings)
        return {"findings": [f.model_dump() for f in findings]}

    @app.get("/api/memory/sources")
    def sources_route() -> list[dict[str, Any]]:
        out = []
        for name, rsc in READ_SOURCES.items():
            snap = TRACKER.snapshot(name)
            out.append({
                "name": name,
                "kind": "read",
                "default_included": rsc.default_included,
                "timeout_s": rsc.timeout,
                **snap,
            })
        return out

    return app
