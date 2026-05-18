"""Lesson-tier read: grep KNOWLEDGE/*.md."""
from __future__ import annotations

from pathlib import Path

from ..types import Hit


class LessonRead:
    name = "lesson"
    read_timeout = 1.0
    default_included = True

    def __init__(self, knowledge_dir: Path) -> None:
        self._dir = knowledge_dir

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in sorted(self._dir.glob("*.md")):
            try:
                content = md.read_text()
            except OSError:
                continue
            if ql not in content.lower():
                continue
            idx = content.lower().find(ql)
            snippet = content[max(0, idx - 200):idx + 400].strip()
            count = content.lower().count(ql)
            hits.append(Hit(
                text=snippet,
                source="lesson/KNOWLEDGE",
                score=0.5 + min(0.4, count * 0.1),
                metadata={"file": str(md)},
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
