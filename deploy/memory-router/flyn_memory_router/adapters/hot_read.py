"""Hot-tier read: grep MEMORY.md sections and pins.json for q."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..types import Hit


class HotRead:
    name = "hot"
    read_timeout = 1.0
    default_included = True

    def __init__(self, memory_md: Path, pin_file: Path) -> None:
        self._md = memory_md
        self._pins = pin_file

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        results: list[Hit] = []
        results.extend(self._scan_pins(q, top_k))
        results.extend(self._scan_sections(q, top_k))
        results.sort(key=lambda h: h.score, reverse=True)
        return results[:top_k]

    def _scan_pins(self, q: str, top_k: int) -> list[Hit]:
        if not self._pins.exists():
            return []
        try:
            pins = json.loads(self._pins.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for p in pins:
            text = f"{p.get('subject', '')}: {p.get('body', '')}".strip(": ")
            if ql in text.lower():
                hits.append(Hit(
                    text=text,
                    source="hot/pins",
                    score=1.0,
                    metadata={"pin_subject": p.get("subject", ""), "ts": p.get("ts", 0)},
                ))
        return hits[:top_k]

    def _scan_sections(self, q: str, top_k: int) -> list[Hit]:
        if not self._md.exists():
            return []
        try:
            content = self._md.read_text()
        except OSError:
            return []
        ql = q.lower()
        sections = re.split(r"(?m)^##\s+", content)
        hits: list[Hit] = []
        for section in sections[1:]:
            head, _, body = section.partition("\n")
            body_text = (head + "\n" + body).strip()
            if ql in body_text.lower():
                count = body_text.lower().count(ql)
                hits.append(Hit(
                    text=body_text[:1000],
                    source="hot/MEMORY.md",
                    score=0.5 + min(0.4, count * 0.1),
                    metadata={"section": head.strip()},
                ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
