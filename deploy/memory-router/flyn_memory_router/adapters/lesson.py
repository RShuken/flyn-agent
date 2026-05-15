"""Lesson-tier adapter: writes/updates a KNOWLEDGE/<NN>-<slug>.md file per the existing pattern.

Existing examples: KNOWLEDGE/02-local-background-routing.md, 09-mcp-agent-turn-gap-investigation.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..types import InboundEvent
from .base import WriteResult


_FRONTMATTER = """---
name: {subject}
description: {description}
type: lesson
---

{body}
"""


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:64]


class LessonKnowledgeAdapter:
    name = "lesson.knowledge_dir"

    def __init__(self, knowledge_dir: Path) -> None:
        self._dir = knowledge_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _next_number(self) -> int:
        existing = list(self._dir.glob("[0-9][0-9]-*.md"))
        nums = []
        for p in existing:
            m = re.match(r"(\d{2})-", p.name)
            if m:
                nums.append(int(m.group(1)))
        return (max(nums) + 1) if nums else 1

    def _find_existing(self, slug: str) -> Path | None:
        for p in self._dir.glob(f"*-{slug}.md"):
            return p
        return None

    def write(self, event: InboundEvent) -> WriteResult:
        slug = _slugify(event.subject)
        existing = self._find_existing(slug)
        if existing is not None:
            path = existing
        else:
            n = self._next_number()
            path = self._dir / f"{n:02d}-{slug}.md"
        description = event.body.splitlines()[0][:140] if event.body else slug
        content = _FRONTMATTER.format(
            subject=slug, description=description, body=event.body,
        )
        path.write_text(content)
        return WriteResult(target=self.name, ok=True, detail=f"wrote {path.name}")
