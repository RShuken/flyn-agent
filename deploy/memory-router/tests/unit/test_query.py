"""Pure-function tests for RRF merge + dedup. No I/O, no adapters."""
from __future__ import annotations

from flyn_memory_router.types import Hit


def _h(text: str, source: str, score: float = 0.9, **meta) -> Hit:
    return Hit(text=text, source=source, score=score, metadata=meta)


def test_rrf_combines_ranks_across_sources():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "hot/MEMORY.md":   [_h("Beth = COO Cora", "hot/MEMORY.md")],
        "warm/graphiti":   [_h("Beth Kukla, co-founder", "warm/graphiti")],
        "reference/wiki":  [_h("Beth — see [[OpenLit]]", "reference/wiki")],
    }
    result = rrf_merge(per_source, top_k=3)
    assert len(result) == 3
    assert all("Beth" in h.text for h in result)
    assert all(h.score > 0 for h in result)


def test_rrf_dedups_by_canonical_id():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "warm/graphiti":  [_h("Beth Kukla", "warm/graphiti", canonical_id="episode-42")],
        "ocw_mem":        [_h("Beth Kukla, COO", "ocw_mem", canonical_id="episode-42")],
        "hot/MEMORY.md":  [_h("Beth = COO Cora", "hot/MEMORY.md")],
    }
    result = rrf_merge(per_source, top_k=10)
    assert len(result) == 2
    merged = next(h for h in result if h.metadata.get("canonical_id") == "episode-42")
    assert "warm/graphiti" in merged.metadata.get("merged_sources", [])
    assert "ocw_mem" in merged.metadata.get("merged_sources", [])


def test_rrf_dedups_by_text_hash():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "hot/MEMORY.md":  [_h("Beth = COO Cora", "hot/MEMORY.md")],
        "warm/graphiti":  [_h("  Beth  =  COO  Cora  ", "warm/graphiti")],
    }
    result = rrf_merge(per_source, top_k=10)
    assert len(result) == 1


def test_rrf_respects_top_k():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "hot/MEMORY.md": [_h(f"hit-{i}", "hot/MEMORY.md", score=1.0 - i * 0.1) for i in range(10)],
    }
    result = rrf_merge(per_source, top_k=3)
    assert len(result) == 3


def test_rrf_handles_empty_sources():
    from flyn_memory_router.query import rrf_merge
    result = rrf_merge({"hot/MEMORY.md": [], "warm/graphiti": []}, top_k=10)
    assert result == []


def test_rrf_k_constant_is_60():
    from flyn_memory_router.query import RRF_K
    assert RRF_K == 60
