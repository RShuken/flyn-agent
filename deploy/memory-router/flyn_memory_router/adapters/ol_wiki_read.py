"""ol-wiki read: REST search at /search with PIN header."""
from __future__ import annotations

import httpx

from ..types import Hit


class OLWikiRead:
    name = "ol_wiki"
    read_timeout = 2.0
    default_included = True

    def __init__(self, url: str, pin: str, http: httpx.AsyncClient | None = None) -> None:
        self._url = url.rstrip("/")
        self._pin = pin
        self._http = http or httpx.AsyncClient(timeout=self.read_timeout)
        self._owns_http = http is None

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        try:
            resp = await self._http.get(
                f"{self._url}/search",
                params={"q": q, "limit": top_k},
                headers={"X-OL-Wiki-Pin": self._pin},
                timeout=self.read_timeout,
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        hits: list[Hit] = []
        for rec in data.get("results", []):
            text = f"{rec.get('question', '')}\n{rec.get('answer', '')}".strip()
            if not text:
                continue
            hits.append(Hit(
                text=text,
                source="ol_wiki",
                score=float(rec.get("score", 0.5)),
                metadata={
                    "question_id": rec.get("id"),
                    "section": rec.get("section"),
                },
            ))
        return hits

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
