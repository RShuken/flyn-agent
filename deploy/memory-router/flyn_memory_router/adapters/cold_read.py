"""Cold-tier read: line-grep the captures index JSONL."""
from __future__ import annotations

import json
from pathlib import Path

from ..types import Hit


class ColdRead:
    name = "cold"
    read_timeout = 1.0
    default_included = True

    def __init__(self, index_path: Path) -> None:
        self._idx = index_path

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._idx.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        try:
            for line in self._idx.read_text().splitlines():
                if ql not in line.lower():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = f"{rec.get('subject', '')}: {rec.get('summary', '')}".strip(": ")
                if not text:
                    continue
                hits.append(Hit(
                    text=text,
                    source="cold/captures",
                    score=0.4,
                    metadata={"ts": rec.get("ts"), "capture_id": rec.get("id")},
                ))
                if len(hits) >= top_k:
                    break
        except OSError:
            return []
        return hits
