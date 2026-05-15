"""Cool-tier adapter: appends to a daily JSONL of cool events under workspace/memory/orchestrator/.

These files are summarized into a single warm-tier markdown by the daily heartbeat
(flyn-orchestrator-daily → memory-rollup). Hard summary caps: ≤2000 chars / ≤8 facts
per day. See spec §2.5.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..types import InboundEvent
from .base import WriteResult


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class CoolDailyRollupAdapter:
    name = "cool.daily_rollup"

    def __init__(self, memory_dir: Path,
                 today: Callable[[], datetime] = _now_utc) -> None:
        self._dir = memory_dir / "orchestrator"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._today = today

    def _path_for_today(self) -> Path:
        date = self._today().strftime("%Y-%m-%d")
        return self._dir / f"{date}-cool-events.jsonl"

    def write(self, event: InboundEvent) -> WriteResult:
        path = self._path_for_today()
        line = json.dumps({
            "ts": _now_utc().isoformat(),
            "source": event.source,
            "event_type": event.event_type,
            "subject": event.subject,
            "body": event.body,
            "dedup_key": event.dedup_key,
        })
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return WriteResult(target=self.name, ok=True, detail=f"appended -> {path.name}")
