"""Cool-tier read: grep daily roll-up files in workspace/memory/."""
from __future__ import annotations

import re
from pathlib import Path

from ..types import Hit

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


class CoolRead:
    name = "cool"
    read_timeout = 1.0
    default_included = True

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in sorted(self._dir.glob("????-??-??.md"), reverse=True):
            m = _DATE_RE.match(md.name)
            if not m:
                continue
            try:
                content = md.read_text()
            except OSError:
                continue
            if ql not in content.lower():
                continue
            idx = content.lower().find(ql)
            snippet = content[max(0, idx - 150):idx + 350].strip()
            hits.append(Hit(
                text=snippet,
                source="cool/rollup",
                score=0.6,
                metadata={"date": m.group(1), "file": str(md)},
            ))
            if len(hits) >= top_k:
                break
        return hits
