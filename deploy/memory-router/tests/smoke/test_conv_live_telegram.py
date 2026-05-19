"""LIVE smoke test for conversation memory.

Run manually after install + service restart:
    cd deploy/memory-router && python3 -m pytest tests/smoke/test_conv_live_telegram.py -v -s

Skips if memory-router is not running on :8400.
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx
import pytest

BASE = "http://localhost:8400"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        try:
            r = c.get("/api/health")
        except httpx.HTTPError:
            pytest.skip("memory-router not running on :8400")
        if r.status_code != 200:
            pytest.skip(f"memory-router unhealthy: {r.status_code}")
        yield c


def test_conv_sources_appears(client):
    """conv adapter shows up in /api/memory/sources."""
    r = client.get("/api/memory/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert "conv" in names


def test_conv_health_cli_runs(client):
    """flyn-mem conv health responds (or skips cleanly if CLI missing)."""
    proc = subprocess.run(
        ["flyn-mem", "conv", "health"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0 and "not found" in (proc.stderr or "").lower():
        pytest.skip("flyn-mem CLI not on PATH; run install.sh first")
    print("\nflyn-mem conv health output:\n", proc.stdout)
    assert proc.returncode == 0


def test_conv_search_after_real_message(client):
    """MANUAL: send a unique message to @flyn_4c_bot before running this test.
    Set FLYN_CONV_LIVE_TEST=1 and (optionally) FLYN_CONV_LIVE_TEXT to enable."""
    if os.environ.get("FLYN_CONV_LIVE_TEST") != "1":
        pytest.skip("Set FLYN_CONV_LIVE_TEST=1 and send a real Telegram message first.")
    unique = os.environ.get("FLYN_CONV_LIVE_TEXT", "FLYN_SMOKE_TOKEN_12345")
    r = client.post("/api/memory/query", json={"q": unique, "top_k": 3})
    body = r.json()
    conv_hits = [h for h in body["hits"] if h["source"].startswith("conv/")]
    assert conv_hits, f"no conv hits found for {unique!r}"
