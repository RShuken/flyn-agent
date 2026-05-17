"""Structured JSONL logging with daily rotation + 90-day/1GB retention."""
from __future__ import annotations

import gzip
import json
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class _JsonlAppender:
    def __init__(self, log_dir: Path, prefix: str) -> None:
        self._dir = log_dir
        self._prefix = prefix
        self._lock = Lock()

    def _path(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / f"{self._prefix}-{_today_iso()}.jsonl"

    def _append(self, record: dict) -> None:
        record["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = json.dumps(record, default=str)
        with self._lock:
            with self._path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class QueryLogWriter(_JsonlAppender):
    def __init__(self, log_dir: Path) -> None:
        super().__init__(log_dir, "query")

    def write(self, record: dict) -> None:
        self._append(record)


class SourceErrorLogWriter(_JsonlAppender):
    def __init__(self, log_dir: Path) -> None:
        super().__init__(log_dir, "source-errors")

    def write(self, query_id: str, source: str, exc: BaseException) -> None:
        self._append({
            "query_id": query_id,
            "source": source,
            "error_class": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exception(exc),
        })


def gc_logs(log_dir: Path,
            retention_days: int = 90,
            max_bytes: int = 1 * 1024 * 1024 * 1024) -> None:
    if not log_dir.exists():
        return
    cutoff = time.time() - retention_days * 86400
    for jsonl in sorted(log_dir.glob("*.jsonl")):
        try:
            mtime = jsonl.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff:
            gz = jsonl.with_suffix(".jsonl.gz")
            with jsonl.open("rb") as fi, gzip.open(gz, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            jsonl.unlink()
    files = sorted(log_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    total = sum(f.stat().st_size for f in files if f.is_file())
    i = 0
    while total > max_bytes and i < len(files):
        f = files[i]
        total -= f.stat().st_size
        f.unlink()
        i += 1
