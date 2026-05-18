"""LIVE schema test — invokes `openclaw memory search --json` if available.

Skips if openclaw is not on PATH. Catches upstream JSON shape changes.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def openclaw_available():
    if shutil.which("openclaw") is None:
        pytest.skip("openclaw CLI not on PATH — skipping live ocw_mem schema test")
    return True


def test_openclaw_memory_search_json_shape(openclaw_available):
    """Verifies `openclaw memory search --json` returns the shape OCWMemRead expects.

    The query "test" is unlikely to return real matches but should always parse cleanly
    as the documented shape (either with `results: []` or `results: [{...}]`).
    """
    proc = subprocess.run(
        ["openclaw", "memory", "search", "--query", "test", "--limit", "3", "--json"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        pytest.skip(f"openclaw memory search exited {proc.returncode}: {proc.stderr.strip()}")

    data = json.loads(proc.stdout or "{}")
    assert "results" in data, f"missing 'results' key; full response: {data}"
    assert isinstance(data["results"], list), f"'results' must be a list; got {type(data['results'])}"

    for rec in data["results"]:
        assert "text" in rec and isinstance(rec["text"], str) and rec["text"], \
            f"record missing/empty 'text': {rec}"
        if "score" in rec:
            assert isinstance(rec["score"], (int, float)), f"'score' must be numeric: {rec}"
        if "line" in rec:
            assert isinstance(rec["line"], int), f"'line' must be int: {rec}"


def test_ocw_mem_read_adapter_against_live(openclaw_available):
    """End-to-end: adapter actually consumes the live CLI output and returns hits."""
    from flyn_memory_router.adapters.ocw_mem_read import OCWMemRead
    hits = asyncio.run(OCWMemRead().query("test", top_k=3))
    # Just verify no exceptions; hits may be empty if no real matches.
    assert isinstance(hits, list)
    for h in hits:
        assert h.source == "ocw_mem"
        assert h.text
        assert isinstance(h.score, (int, float))
