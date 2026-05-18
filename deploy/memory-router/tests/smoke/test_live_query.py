"""LIVE smoke test — hits the actually-running flyn-memory-router service.

Run manually after install.sh:
    cd deploy/memory-router && python3 -m pytest tests/smoke/ -v -s

Excluded from the default pytest run via pyproject testpaths.
"""
from __future__ import annotations

import httpx
import pytest

BASE = "http://localhost:8400"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        r = c.get("/api/health")
        if r.status_code != 200:
            pytest.skip("flyn-memory-router not running on :8400")
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_sources_lists_all_adapters(client):
    r = client.get("/api/memory/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    expected = {"hot", "warm", "cool", "cold", "lesson", "reference", "user",
                "ol_wiki", "ocw_mem", "lossless"}
    assert expected.issubset(names)


def test_query_smoke(client):
    r = client.post("/api/memory/query", json={"q": "Flyn", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert "query_id" in body
    assert "elapsed_ms" in body
    print(f"\nquery_id={body['query_id']} elapsed_ms={body['elapsed_ms']}")
    print(f"hits returned: {len(body['hits'])}")
    print(f"source_errors: {[e['source'] for e in body['source_errors']]}")


def test_query_respects_include_filter(client):
    r = client.post("/api/memory/query", json={"q": "test", "include": ["hot"], "top_k": 5})
    assert r.status_code == 200
    sources_seen = {h["source"].split("/")[0] for h in r.json()["hits"]}
    if sources_seen:
        assert sources_seen == {"hot"}


def test_logs_write_correlates(client):
    import os, datetime, pathlib
    log_dir = pathlib.Path(os.environ.get("FLYN_MEMORY_ROUTER_HOME",
                                           str(pathlib.Path.home() / ".flyn" / "memory-router"))) / "logs"
    today = datetime.date.today().isoformat()
    today_log = log_dir / f"query-{today}.jsonl"
    r = client.post("/api/memory/query", json={"q": "smoke-test-marker", "top_k": 1})
    qid = r.json()["query_id"]
    assert today_log.exists()
    found = any(qid in line for line in today_log.read_text().splitlines())
    assert found, f"query_id {qid} not found in {today_log}"
