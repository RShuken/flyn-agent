"""Drift detection across read sources.

Strategy: for an entity, run the standard query; pairwise-compare top hits
per source by token Jaccard; if any pair < threshold, emit one finding.
Reported, never auto-resolved.
"""
from __future__ import annotations

import re

from .types import Hit, LintFinding


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.lower()))


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def detect_drift(entity: str,
                        per_source: dict[str, list[Hit]],
                        threshold: float = 0.6) -> list[LintFinding]:
    top_per_source: dict[str, str] = {}
    for src, hits in per_source.items():
        if hits:
            top_per_source[src] = hits[0].text
    if len(top_per_source) < 2:
        return []
    diverged = False
    keys = list(top_per_source.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if _jaccard(top_per_source[keys[i]], top_per_source[keys[j]]) < threshold:
                diverged = True
                break
        if diverged:
            break
    if not diverged:
        return []
    return [LintFinding(
        entity=entity,
        sources=top_per_source,
        divergence=f"Pairwise Jaccard < {threshold} between {len(keys)} sources",
        suggested_fix="Review and reconcile; canonical source is typically Graphiti for facts.",
    )]
