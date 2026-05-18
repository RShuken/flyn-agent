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


def test_query_retries_once_on_5xx(capsys, monkeypatch):
    from flyn_memory_router.cli import main
    import flyn_memory_router.cli as cli_module

    monkeypatch.setattr(cli_module._time, "sleep", lambda _: None)  # no real sleep in tests

    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json={
            "query_id": "q-y",
            "hits": [],
            "source_errors": [],
            "elapsed_ms": 10,
        })

    rc = main(["query", "x"], client_factory=_client_factory(handler))
    assert rc == 0
    assert call_count["n"] == 2


def test_ingest_subcommand_posts_event(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        assert request.url.path == "/api/memory/ingest"
        body = json.loads(request.content)
        assert body["source"] == "manual"
        assert body["event_type"] == "test"
        return httpx.Response(200, json={
            "accepted": True, "deduped": False, "importance": "warm",
            "tiers_written": ["warm"], "notes": [],
        })

    event_json = json.dumps({
        "source": "manual", "event_type": "test", "subject": "x",
        "body": "hello", "dedup_key": "k1",
    })
    rc = main(["ingest", event_json], client_factory=_client_factory(handler))
    captured = capsys.readouterr()
    assert rc == 0
    assert "accepted" in captured.out
    assert "warm" in captured.out


def test_ingest_subcommand_rejects_invalid_json(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        return httpx.Response(200, json={})

    rc = main(["ingest", "not-valid-json"], client_factory=_client_factory(handler))
    err = capsys.readouterr().err
    assert rc != 0
    assert "JSON" in err or "json" in err


def test_ingest_subcommand_propagates_server_400(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        return httpx.Response(400, json={"detail": "missing dedup_key"})

    event_json = json.dumps({"source": "x", "event_type": "y", "subject": "z", "body": "w", "dedup_key": "k"})
    rc = main(["ingest", event_json], client_factory=_client_factory(handler))
    err = capsys.readouterr().err
    assert rc != 0
    assert "400" in err
