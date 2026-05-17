"""Rolling per-source success/error stats (in-memory, process-local)."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Any


class HealthTracker:
    def __init__(self, window: int = 100) -> None:
        self._window = window
        self._stats: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def record(self, source: str, elapsed_ms: int, error: bool) -> None:
        with self._lock:
            row = self._stats.setdefault(source, {
                "last_elapsed_ms": None,
                "last_error_ts": None,
                "samples": deque(maxlen=self._window),
            })
            row["last_elapsed_ms"] = elapsed_ms
            if error:
                row["last_error_ts"] = time.time()
            row["samples"].append(1 if error else 0)

    def snapshot(self, source: str) -> dict[str, Any]:
        with self._lock:
            row = self._stats.get(source)
            if row is None:
                return {"last_elapsed_ms": None, "last_error_ts": None, "error_rate_100q": None}
            samples = row["samples"]
            rate = (sum(samples) / len(samples)) if samples else 0.0
            return {
                "last_elapsed_ms": row["last_elapsed_ms"],
                "last_error_ts": row["last_error_ts"],
                "error_rate_100q": rate,
            }

    def all_snapshots(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {src: self.snapshot(src) for src in self._stats}


TRACKER = HealthTracker(window=100)
