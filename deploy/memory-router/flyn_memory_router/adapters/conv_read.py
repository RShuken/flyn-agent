"""Conversation-tier read adapter — 11th adapter in the existing fan-out.

Queries each accessible owner's ConvDb via FTS5 over body + summary.
Cross-owner reads write to audit_log via the OwnerRegistry. Returns
Hit objects compatible with the existing query.py RRF merge.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..conv.owner import OwnerRegistry
from ..conv.schema import ConvDb
from ..types import Hit


class ConvReadAdapter:
    name: str = "conv"
    read_timeout: float = 1.5
    default_included: bool = True

    def __init__(
        self,
        registry: OwnerRegistry,
        conv_root: Path,
        viewer_id: Optional[str] = None,
    ) -> None:
        self._registry = registry
        self._conv_root = conv_root
        self._viewer = viewer_id or os.environ.get("USER", "ryan")

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        accessible = self._registry.list_accessible_owners(self._viewer)
        all_hits: list[Hit] = []
        for owner_id in accessible:
            db_path = self._registry.db_path_for(owner_id, self._conv_root)
            if not db_path.exists():
                continue
            db = ConvDb(owner_id, db_path)
            for stored in db.search(q, top_k=top_k):
                all_hits.append(Hit(
                    text=stored.summary or stored.body[:500],
                    source=f"conv/{stored.channel}",
                    score=stored.fts_score,
                    metadata={
                        "msg_id": stored.row_id,
                        "thread_id": stored.thread_id,
                        "sender_id": stored.sender_id,
                        "ts": stored.ts,
                        "owner": owner_id,
                        "has_summary": stored.summary is not None,
                    },
                ))
            if owner_id != self._viewer:
                self._registry.append_audit(self._viewer, owner_id, op="read", q=q)
        all_hits.sort(key=lambda h: h.score, reverse=True)
        return all_hits[:top_k]
