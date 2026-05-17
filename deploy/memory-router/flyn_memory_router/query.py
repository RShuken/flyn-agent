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
