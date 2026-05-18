"""Reference-tier read: walk the Karpathy LLM Wiki at vault/wiki/.

Strategy per the vault's CLAUDE.md schema: read wiki/index.md first to
get the catalog, then walk wiki/*.md for text matches. Follow [[wikilinks]]
to surface adjacent pages.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..types import Hit

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


class ReferenceRead:
    name = "reference"
    read_timeout = 1.5
    default_included = True

    def __init__(self, vault: Path) -> None:
        self._vault = vault
        self._wiki = vault / "wiki"
        self._index_cache: dict[str, Path] | None = None

    def _resolve_target(self, name: str) -> Path | None:
        """Resolve a wikilink target name to a path using the index cache.

        Builds the cache lazily if not already populated.
        Returns None if the target is not found.
        """
        if self._index_cache is None:
            self._index_cache = {p.stem: p for p in self._wiki.rglob("*.md") if p.name not in ("log.md", "index.md")}
        return self._index_cache.get(name.strip())

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._wiki.exists():
            return []
        index_path = self._wiki / "index.md"
        if not index_path.exists():
            return []

        # Build the index cache once per query
        self._index_cache = {p.stem: p for p in self._wiki.rglob("*.md") if p.name not in ("log.md", "index.md")}

        ql = q.lower()
        candidates: list[Path] = []

        for md in self._wiki.rglob("*.md"):
            if md.name in ("log.md", "index.md"):
                continue
            try:
                if ql in md.read_text().lower():
                    candidates.append(md)
            except OSError:
                continue

        adjacent: set[Path] = set()
        for cand in candidates:
            try:
                content = cand.read_text()
            except OSError:
                continue
            for match in _WIKILINK_RE.finditer(content):
                target = match.group(1).strip()
                target_path = self._resolve_target(target)
                if target_path and target_path != cand:
                    adjacent.add(target_path)

        hits: list[Hit] = []
        for path in candidates:
            try:
                content = path.read_text()
            except OSError:
                continue
            idx = content.lower().find(ql)
            snippet = content[max(0, idx - 200):idx + 400].strip()
            hits.append(Hit(
                text=snippet,
                source="reference/wiki",
                score=0.8,
                metadata={"file": str(path), "via": "direct_match"},
            ))
        for path in adjacent:
            if path in candidates:
                continue
            try:
                content = path.read_text()
            except OSError:
                continue
            hits.append(Hit(
                text=content[:500].strip(),
                source="reference/wiki",
                score=0.5,
                metadata={"file": str(path), "via": "wikilink"},
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
