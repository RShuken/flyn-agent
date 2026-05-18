"""User-tier read: Claude Code auto-memory at ~/.claude/projects/.../memory/."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..types import Hit


def _split_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end < 0:
        return {}, content
    try:
        meta = yaml.safe_load(content[4:end]) or {}
    except yaml.YAMLError:
        meta = {}
    body = content[end + 5:]
    return meta, body


class UserRead:
    name = "user"
    read_timeout = 1.0
    default_included = True

    def __init__(self, auto_memory_dir: Path) -> None:
        self._dir = auto_memory_dir

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in sorted(self._dir.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            try:
                content = md.read_text()
            except OSError:
                continue
            meta, body = _split_frontmatter(content)
            if ql not in body.lower() and ql not in str(meta).lower():
                continue
            idx_in_body = body.lower().find(ql)
            if idx_in_body >= 0:
                snippet = body[max(0, idx_in_body - 200):idx_in_body + 400].strip()
            else:
                snippet = body.strip()[:500]
            hits.append(Hit(
                text=snippet,
                source="user/auto-memory",
                score=0.7,
                metadata={
                    "file": str(md),
                    "name": meta.get("name", ""),
                    "memory_type": (meta.get("metadata") or {}).get("type", ""),
                    "description": meta.get("description", ""),
                },
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
