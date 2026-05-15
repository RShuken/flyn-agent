"""Disk-persisted backpressure queue.

Phase 0 status: class is built and tested but NOT yet wired into Router.ingest().
Phase 1 will add: on adapter-failure -> EventQueue.enqueue(); periodic drain via
the daily heartbeat or a maintenance endpoint. Until then, failed adapter writes
are recorded in EventResult.notes and lost on process death.

The router enqueues events whose downstream adapters all failed (typically:
Graphiti slow / Gemini quota). A periodic replay job drains and re-tries.

Files: NNNNNNNNN-<dedup_key>.json with a monotonic integer prefix. Drain
returns in filename-sort order (insertion order). Corrupted files move to
`./quarantine/` so the queue can keep moving.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

from .types import InboundEvent


class EventQueue:
    def __init__(self, queue_dir: Path) -> None:
        self._dir = queue_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._quarantine = queue_dir / "quarantine"
        self._quarantine.mkdir(parents=True, exist_ok=True)

    def _next_filename(self, dedup_key: str) -> Path:
        ts = int(time.time() * 1000)
        safe_key = "".join(c if c.isalnum() or c in "-_" else "-" for c in dedup_key)[:64]
        return self._dir / f"{ts:013d}-{safe_key}.json"

    def enqueue(self, event: InboundEvent) -> None:
        path = self._next_filename(event.dedup_key)
        path.write_text(event.model_dump_json())

    def drain(self) -> Iterator[InboundEvent]:
        files = sorted(p for p in self._dir.iterdir() if p.suffix == ".json")
        for p in files:
            try:
                data = json.loads(p.read_text())
                ev = InboundEvent.model_validate(data)
            except Exception:  # noqa: BLE001 — anything malformed gets quarantined
                target = self._quarantine / p.name
                p.rename(target)
                continue
            p.unlink()
            yield ev

    def size(self) -> int:
        return sum(1 for p in self._dir.iterdir() if p.suffix == ".json")
