"""Cross-source query orchestration: dedup + RRF merge.

This module splits into pure functions (this file) and the async orchestrator
entry point `query()` added in Task 35. Phase 0c ships only pure functions
to keep early tests free of I/O.
"""
from __future__ import annotations

import hashlib
import re

from .types import Hit

RRF_K = 60  # reciprocal-rank-fusion constant (Cormack, Clarke, Buettcher 2009)


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _text_hash(s: str) -> str:
    return hashlib.sha256(_normalize_text(s).encode("utf-8")).hexdigest()


def _hit_canonical_key(h: Hit) -> str:
    cid = h.metadata.get("canonical_id")
    return f"cid:{cid}" if cid else f"th:{_text_hash(h.text)}"


def _merge_two_hits(a: Hit, b: Hit) -> Hit:
    merged_sources: list[str] = list(a.metadata.get("merged_sources") or [a.source])
    if b.source not in merged_sources:
        merged_sources.append(b.source)
    new_meta = {**a.metadata, **{k: v for k, v in b.metadata.items() if k not in a.metadata}}
    new_meta["merged_sources"] = merged_sources
    return Hit(text=a.text, source=a.source, score=a.score, metadata=new_meta)


def rrf_merge(per_source: dict[str, list[Hit]], top_k: int) -> list[Hit]:
    """Merge per-source hits into a ranked list via reciprocal rank fusion.

    Hits with the same canonical_id (or, lacking that, normalized-text hash)
    are collapsed BEFORE RRF scoring. The collapsed hit's RRF score
    accumulates from all sources where it appeared.
    """
    bucket: dict[str, tuple[Hit, list[tuple[str, int]]]] = {}
    for source, hits in per_source.items():
        for rank, hit in enumerate(hits[:max(top_k * 3, 50)]):
            key = _hit_canonical_key(hit)
            if key in bucket:
                rep, observations = bucket[key]
                rep = _merge_two_hits(rep, hit)
                observations.append((source, rank))
                bucket[key] = (rep, observations)
            else:
                bucket[key] = (hit, [(source, rank)])

    scored: list[tuple[float, Hit]] = []
    for _key, (rep, observations) in bucket.items():
        rrf_score = sum(1.0 / (RRF_K + rank) for _src, rank in observations)
        scored_hit = Hit(text=rep.text, source=rep.source, score=rrf_score, metadata=rep.metadata)
        scored.append((rrf_score, scored_hit))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [h for _s, h in scored[:top_k]]


def normalize_text(s: str) -> str:
    return _normalize_text(s)


def text_hash(s: str) -> str:
    return _text_hash(s)


# --- async orchestrator added in Task 35 ---
import asyncio
import importlib
import time
import uuid as _uuid

from .config import Config, READ_SOURCES, ReadSourceConfig
from .health_tracker import TRACKER
from .types import QueryResult, SourceError


def _resolve_class(cls_path: str):
    module_path, _, cls_name = cls_path.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def _construct(rsc: ReadSourceConfig, cfg: Config):
    cls = _resolve_class(rsc.cls_path)
    name = rsc.name
    if name == "hot":
        return cls(memory_md=cfg.memory_md, pin_file=cfg.pin_file)
    if name == "warm":
        return cls(graphiti_url=cfg.graphiti_url, workspace_memory_dir=cfg.workspace_memory_dir)
    if name == "cool":
        return cls(memory_dir=cfg.workspace_memory_dir)
    if name == "cold":
        return cls(index_path=cfg.captures_index)
    if name == "lesson":
        return cls(knowledge_dir=cfg.knowledge_dir)
    if name == "reference":
        return cls(vault=cfg.reference_vault)
    if name == "user":
        return cls(auto_memory_dir=cfg.auto_memory_dir)
    if name == "ol_wiki":
        return cls(url=cfg.ol_wiki_url, pin=cfg.ol_wiki_pin)
    if name == "ocw_mem":
        return cls()
    if name == "lossless":
        return cls()
    raise KeyError(f"No constructor wiring for adapter {name!r}")


def _load_adapters(include: list[str] | None, exclude: list[str] | None):
    """Construct active read adapters per request. Override in tests via monkeypatch."""
    cfg = Config.from_env()
    inc = set(include) if include else None
    exc = set(exclude or [])

    selected: list[ReadSourceConfig] = []
    for name, rsc in READ_SOURCES.items():
        if inc is not None:
            if name not in inc:
                continue
        else:
            if not rsc.default_included:
                continue
        if name in exc:
            continue
        selected.append(rsc)

    return [_construct(rsc, cfg) for rsc in selected]


async def query(q: str,
                include: list[str] | None = None,
                exclude: list[str] | None = None,
                top_k: int = 10) -> QueryResult:
    """Fan out across configured ReadAdapters, gather, dedup + RRF, return."""
    qid = "q-" + _uuid.uuid4().hex[:12]
    start = time.monotonic()
    adapters = _load_adapters(include, exclude)
    if not adapters:
        return QueryResult(query_id=qid, hits=[], source_errors=[], elapsed_ms=0)

    async def _one(adapter):
        return await asyncio.wait_for(adapter.query(q, top_k=top_k),
                                       timeout=adapter.read_timeout)

    results = await asyncio.gather(
        *[_one(a) for a in adapters],
        return_exceptions=True,
    )

    per_source: dict[str, list[Hit]] = {}
    errors: list[SourceError] = []
    for adapter, result in zip(adapters, results):
        if isinstance(result, asyncio.TimeoutError):
            TRACKER.record(adapter.name, elapsed_ms=int(adapter.read_timeout * 1000), error=True)
            errors.append(SourceError(source=adapter.name, error_class="timeout",
                                       message=f"{adapter.read_timeout}s"))
            continue
        if isinstance(result, Exception):
            TRACKER.record(adapter.name, elapsed_ms=0, error=True)
            errors.append(SourceError(source=adapter.name, error_class="exception",
                                       message=f"{type(result).__name__}: {result}"))
            continue
        TRACKER.record(adapter.name, elapsed_ms=int((time.monotonic() - start) * 1000), error=False)
        per_source[adapter.name] = result

    merged = rrf_merge(per_source, top_k=top_k)
    elapsed = int((time.monotonic() - start) * 1000)
    return QueryResult(query_id=qid, hits=merged, source_errors=errors, elapsed_ms=elapsed)
