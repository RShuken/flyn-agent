from __future__ import annotations

import json

import httpx
import pytest


def _client_factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.Client(transport=transport, base_url="http://localhost:8400")


def test_query_subcommand_prints_hits(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        body = json.loads(request.content)
        assert body["q"] == "Beth"
        return httpx.Response(200, json={
            "query_id": "q-abc",
            "hits": [{"text": "Beth = COO", "source": "hot/MEMORY.md", "score": 0.95, "metadata": {}}],
            "source_errors": [],
            "elapsed_ms": 42,
        })

    rc = main(["query", "Beth"], client_factory=_client_factory(handler))
    captured = capsys.readouterr()
    assert rc == 0
    assert "Beth = COO" in captured.out


def test_query_json_flag(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        return httpx.Response(200, json={"query_id": "q-x", "hits": [], "source_errors": [], "elapsed_ms": 0})

    rc = main(["query", "anything", "--json"], client_factory=_client_factory(handler))
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["query_id"] == "q-x"


def test_health_subcommand(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "service": "flyn-memory-router", "port": 8400})
        if request.url.path == "/api/memory/sources":
            return httpx.Response(200, json=[{"name": "hot", "default_included": True,
                                                "last_elapsed_ms": 5, "error_rate_100q": 0.0}])
        return httpx.Response(404)

    rc = main(["health"], client_factory=_client_factory(handler))
    assert rc == 0


def test_query_unreachable_prints_actionable_error(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    rc = main(["query", "Beth"], client_factory=_client_factory(handler))
    err = capsys.readouterr().err
    assert rc != 0
    assert "launchctl" in err
