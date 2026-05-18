from __future__ import annotations

import json
from pathlib import Path


def test_query_log_writer_appends_jsonl(tmp_path: Path):
    from flyn_memory_router.logging_contract import QueryLogWriter
    log_dir = tmp_path / "logs"
    w = QueryLogWriter(log_dir=log_dir)
    w.write({
        "query_id": "q-1", "q": "Beth", "caller": "cli",
        "included_sources": ["hot", "warm"],
        "per_source": {"hot": {"hits": 1, "elapsed_ms": 5},
                       "warm": {"hits": 2, "elapsed_ms": 40}},
        "total_elapsed_ms": 45, "top_k": 10,
    })
    files = list(log_dir.glob("query-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().strip().splitlines()[-1])
    assert rec["query_id"] == "q-1"
    assert "ts" in rec


def test_source_error_log_correlated_by_query_id(tmp_path: Path):
    from flyn_memory_router.logging_contract import SourceErrorLogWriter
    log_dir = tmp_path / "logs"
    w = SourceErrorLogWriter(log_dir=log_dir)
    w.write(query_id="q-1", source="ocw_mem", exc=RuntimeError("boom"))
    files = list(log_dir.glob("source-errors-*.jsonl"))
    assert files
    rec = json.loads(files[0].read_text().strip().splitlines()[-1])
    assert rec["query_id"] == "q-1"
    assert rec["source"] == "ocw_mem"
    assert "RuntimeError" in rec["error_class"]


def test_rotation_creates_daily_files(tmp_path: Path, monkeypatch):
    from flyn_memory_router import logging_contract as lc
    log_dir = tmp_path / "logs"
    w = lc.QueryLogWriter(log_dir=log_dir)
    monkeypatch.setattr(lc, "_today_iso", lambda: "2026-05-10")
    w.write({"query_id": "q-1", "q": "x", "caller": "test",
             "included_sources": [], "per_source": {}, "total_elapsed_ms": 0, "top_k": 0})
    monkeypatch.setattr(lc, "_today_iso", lambda: "2026-05-11")
    w.write({"query_id": "q-2", "q": "y", "caller": "test",
             "included_sources": [], "per_source": {}, "total_elapsed_ms": 0, "top_k": 0})
    files = sorted(p.name for p in log_dir.glob("query-*.jsonl"))
    assert files == ["query-2026-05-10.jsonl", "query-2026-05-11.jsonl"]
