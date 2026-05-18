"""Integration: real FastAPI app, fake adapters, full POST cycle."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyn_memory_router.server import build_app
from flyn_memory_router.types import Hit


class _FakeRead:
    def __init__(self, name: str, hits: list[Hit] | None = None,
                 default_included: bool = True, timeout: float = 1.0):
        self.name = name
        self.default_included = default_included
        self.read_timeout = timeout
        self._hits = hits or []

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        return self._hits


@pytest.fixture
def app_with_fakes(monkeypatch, tmp_path):
    from flyn_memory_router import query as query_module
    fakes = [
        _FakeRead("hot", [Hit(text="Beth Kukla, COO", source="hot/MEMORY.md", score=0.9, metadata={})]),
        _FakeRead("warm", [Hit(text="Beth episode", source="warm/graphiti", score=0.8,
                                metadata={"canonical_id": "ep-1"})]),
        _FakeRead("reference", [Hit(text="Beth: see [[openlit]]", source="reference/wiki",
                                     score=0.7, metadata={})]),
    ]
    monkeypatch.setattr(query_module, "_load_adapters", lambda include, exclude: fakes)
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    app = build_app()
    return TestClient(app)


def test_query_returns_merged_hits(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": "Beth"})
    assert resp.status_code == 200
    body = resp.json()
    assert "query_id" in body
    assert body["query_id"].startswith("q-")
    assert len(body["hits"]) >= 1


def test_query_top_k_clamps_results(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": "Beth", "top_k": 1})
    assert resp.status_code == 200
    assert len(resp.json()["hits"]) == 1


def test_query_validation_rejects_empty_q(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": ""})
    assert resp.status_code == 422


def test_query_records_per_source_elapsed(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": "Beth"})
    body = resp.json()
    assert resp.status_code == 200
    # The orchestrator records elapsed per source; verify TRACKER received a non-None value
    from flyn_memory_router.health_tracker import TRACKER
    for src in ("hot", "warm", "reference"):
        snap = TRACKER.snapshot(src)
        assert snap["last_elapsed_ms"] is not None


@pytest.fixture
def app_with_all_failing(monkeypatch, tmp_path):
    from flyn_memory_router import query as query_module

    class _ThrowingAdapter:
        def __init__(self, name):
            self.name = name
            self.default_included = True
            self.read_timeout = 1.0

        async def query(self, q, top_k=10):
            raise RuntimeError("boom")

    monkeypatch.setattr(query_module, "_load_adapters",
                        lambda include, exclude: [_ThrowingAdapter("hot"), _ThrowingAdapter("warm")])
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    app = build_app()
    return TestClient(app)


def test_query_returns_502_when_all_sources_fail(app_with_all_failing):
    resp = app_with_all_failing.post("/api/memory/query", json={"q": "test"})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert len(detail["source_errors"]) == 2
    assert "query_id" in detail


def test_build_app_runs_gc_logs_on_startup(monkeypatch, tmp_path):
    """gc_logs should be invoked once at app build time."""
    from flyn_memory_router import server as server_module
    calls = []

    def fake_gc_logs(log_dir, retention_days=90, max_bytes=None):
        calls.append({"log_dir": str(log_dir), "retention_days": retention_days})

    monkeypatch.setattr(server_module, "gc_logs", fake_gc_logs)
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    server_module.build_app()
    assert len(calls) >= 1
    assert "logs" in calls[0]["log_dir"]


def test_lint_uses_autodiscovery_when_no_entities(monkeypatch, tmp_path):
    """When /api/memory/lint receives no entities, it pulls from wiki/index.md."""
    from flyn_memory_router import query as query_module
    from flyn_memory_router.types import Hit

    # Build a vault fixture with an index pointing at "beth"
    vault = tmp_path / "vault"
    wiki = vault / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "index.md").write_text("# Index\n- [[beth]]\n")

    # Fake adapters that return agreeing hits for "beth" (no drift)
    class _FakeR:
        def __init__(self, name):
            self.name = name
            self.default_included = True
            self.read_timeout = 1.0

        async def query(self, q, top_k=10):
            return [Hit(text="Beth Kukla, COO Cora", source=f"{self.name}/test", score=0.9, metadata={})]

    monkeypatch.setattr(query_module, "_load_adapters",
                        lambda include, exclude: [_FakeR("hot"), _FakeR("warm")])
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("FLYN_REFERENCE_VAULT", str(vault))
    client = TestClient(build_app())

    # Send lint with NO entities field — should autodiscover "beth" from vault
    resp = client.post("/api/memory/lint", json={})
    assert resp.status_code == 200
    # No drift expected (both sources agree); findings list is empty but route succeeded
    assert resp.json() == {"findings": []}
