"""Conversation-tier write adapter.

Triggered when InboundEvent.event_type == "conversation_message". Routes
to the appropriate per-owner ConvDb after sealing the raw_payload with
the owner's AES-GCM key. Fire-and-forget POST to Graphiti for entity
extraction. Async summarizer job enqueued.

All five steps wrapped in try/except → never raises to the ingest pipeline;
failure surfaces as WriteResult(ok=False, detail=...).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .base import WriteResult
from ..conv import encrypted_raw
from ..conv.owner import OwnerRegistry
from ..conv.schema import ConvDb, ConvMessage
from ..conv.summarizer import SummarizeJob, enqueue
from ..types import InboundEvent

logger = logging.getLogger(__name__)


class ConvWriteAdapter:
    name: str = "conv.telegram"

    def __init__(
        self,
        registry: OwnerRegistry,
        conv_root: Path,
        queue_dir: Path,
        graphiti_url: Optional[str],
        http_client=None,
    ) -> None:
        self._registry = registry
        self._conv_root = conv_root
        self._queue_dir = queue_dir
        self._graphiti_url = graphiti_url
        self._http = http_client  # httpx.Client or None
        self._conv_root.mkdir(parents=True, exist_ok=True)

    def write(self, event: InboundEvent) -> WriteResult:
        raw = event.raw_payload or {}
        channel = raw.get("channel", event.source)
        sender_id = str(raw.get("sender_id") or raw.get("chat_id") or "")

        # 1. Owner resolution
        owner = self._registry.resolve_from_chat(channel, sender_id)
        if owner is None:
            return WriteResult(target=self.name, ok=False,
                               detail=f"unknown sender: channel={channel} sender_id={sender_id}")

        # 2. Seal raw_payload
        try:
            sealed = encrypted_raw.seal(
                json.dumps(raw, default=str).encode("utf-8"),
                owner.id,
            )
        except encrypted_raw.KeychainLocked as exc:
            return WriteResult(target=self.name, ok=False, detail=f"keychain locked: {exc}")
        except Exception as exc:
            return WriteResult(target=self.name, ok=False, detail=f"seal failed: {exc}")

        # 3. Write to per-owner ConvDb
        try:
            db = ConvDb(owner.id, self._registry.db_path_for(owner.id, self._conv_root))
            ts = str(raw.get("ts") or (event.valid_at.isoformat() if event.valid_at else ""))
            msg = ConvMessage(
                channel=channel,
                sender_id=sender_id,
                thread_id=str(raw.get("thread_id") or raw.get("chat_id") or ""),
                reply_to_id=raw.get("reply_to_msg_id"),
                ts=ts,
                body=event.body,
                attachments=raw.get("attachments", []),
                encrypted_raw=sealed,
            )
            row_id = db.write(msg)
        except Exception as exc:
            return WriteResult(target=self.name, ok=False, detail=f"db.write failed: {exc}")

        # 4. Enqueue summarize-job (best-effort)
        try:
            enqueue(self._queue_dir, SummarizeJob(
                owner_id=owner.id,
                db_path=str(db.path),
                row_id=row_id,
                body=msg.body,
                sender_id=msg.sender_id,
            ))
        except Exception as exc:
            logger.warning("conv: enqueue summarize failed: %s", exc)

        # 5. POST Graphiti episode (fire-and-forget)
        if self._graphiti_url and self._http is not None:
            try:
                self._http.post(
                    f"{self._graphiti_url.rstrip('/')}/api/episodes",
                    json={
                        "name": f"telegram-msg-{row_id}",
                        "summary": msg.body,
                        "group_id": f"flyn-{owner.id}",
                        "source_description": "conv/telegram",
                        "valid_at": msg.ts,
                    },
                    timeout=2.0,
                )
            except Exception as exc:
                logger.debug("conv: graphiti POST failed: %s", exc)

        return WriteResult(target=self.name, ok=True, detail=f"row={row_id}")
