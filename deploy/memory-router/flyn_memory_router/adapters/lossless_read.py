"""lossless read: grep Lossless Claw plugin's on-disk session logs."""
from __future__ import annotations

import json
from pathlib import Path

from ..types import Hit


class LosslessRead:
    name = "lossless"
    read_timeout = 3.0
    default_included = False

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._dir = sessions_dir or (
            Path.home() / ".openclaw" / "plugins" / "lossless-claw" / "sessions"
        )

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for jsonl in sorted(self._dir.glob("*.jsonl"), reverse=True):
            try:
                for line in jsonl.read_text().splitlines():
                    if ql not in line.lower():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = rec.get("content") or ""
                    if not content or ql not in content.lower():
                        continue
                    hits.append(Hit(
                        text=content,
                        source="lossless",
                        score=0.3,
                        metadata={
                            "session_file": str(jsonl),
                            "role": rec.get("role", ""),
                        },
                    ))
                    if len(hits) >= top_k:
                        return hits
            except OSError:
                continue
        return hits
