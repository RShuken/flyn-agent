"""Cold-tier adapter: append-only index of raw captures.

Phase 0 deliberately keeps this minimal — the actual capture files live with
the orchestrator (Phase 1), which writes them under
`~/.flyn/orchestrator/captures/<task-id>/<worker-id>.jsonl`. The router's
cold adapter maintains a one-line-per-event index so it's queryable later.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..types import InboundEvent
from .base import WriteResult


class ColdCapturesIndexAdapter:
    name = "cold.captures_index"

    def __init__(self, index_path: Path) -> None:
        self._path = index_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: InboundEvent) -> WriteResult:
        line = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": event.source,
            "event_type": event.event_type,
            "subject": event.subject,
            "dedup_key": event.dedup_key,
        })
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return WriteResult(target=self.name, ok=True, detail=f"appended -> {self._path.name}")
