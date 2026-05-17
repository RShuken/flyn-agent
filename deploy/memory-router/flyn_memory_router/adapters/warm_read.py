"""Warm-tier read: Graphiti REST + workspace/memory/*.md grep."""
from __future__ import annotations

from pathlib import Path

import httpx

from ..types import Hit


class WarmRead:
    name = "warm"
    read_timeout = 2.0
    default_included = True

    def __init__(
        self,
        graphiti_url: str,
        workspace_memory_dir: Path,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = graphiti_url.rstrip("/")
        self._dir = workspace_memory_dir
        self._http = http or httpx.AsyncClient(timeout=2.0)
        self._owns_http = http is None

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        graphiti = await self._graphiti(q, top_k)
        workspace = self._workspace(q, top_k)
        combined = graphiti + workspace
        combined.sort(key=lambda h: h.score, reverse=True)
        return combined[:top_k]

    async def _graphiti(self, q: str, top_k: int) -> list[Hit]:
        try:
            resp = await self._http.get(
                f"{self._url}/api/search",
                params={"q": q, "limit": top_k},
                timeout=self.read_timeout,
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        hits: list[Hit] = []
        for ep in data.get("results", []):
            text = ep.get("summary") or ep.get("name") or ""
            if not text:
                continue
            hits.append(Hit(
                text=text,
                source="warm/graphiti",
                score=float(ep.get("score", 0.5)),
                metadata={
                    "canonical_id": ep.get("uuid"),
                    "name": ep.get("name"),
                },
            ))
        return hits

    def _workspace(self, q: str, top_k: int) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in self._dir.glob("*.md"):
            try:
                content = md.read_text()
            except OSError:
                continue
            if ql not in content.lower():
                continue
            idx = content.lower().find(ql)
            start = max(0, idx - 200)
            end = min(len(content), idx + 200 + len(q))
            snippet = content[start:end].strip()
            count = content.lower().count(ql)
            hits.append(Hit(
                text=snippet,
                source="warm/workspace",
                score=0.5 + min(0.4, count * 0.1),
                metadata={"file": str(md)},
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
