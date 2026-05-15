"""TelegramChannelAdapter — wraps @flyn_4c_bot for ingest + send + approve buttons.

Reads bot token from ~/.openclaw/openclaw.json (channels.telegram.botToken)
OR from auth-profiles.json (telegram:default). For MVP, no webhook server is
provided — callers POST raw Updates to /api/tasks/inbound which routes
through this adapter's ingest() method.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from ...types import InboundTaskRequest, SenderRole


# Hardcoded for MVP — Phase 1b will read from CONTACTS.md
RYAN_CHAT_ID = 7191564227
BETH_CHAT_ID = 7434192034


def _load_bot_token() -> str:
    p = Path.home() / ".openclaw" / "openclaw.json"
    if p.exists():
        try:
            d = json.load(open(p))
            t = d.get("channels", {}).get("telegram", {}).get("botToken")
            if t:
                return t
        except Exception:
            pass
    p2 = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if p2.exists():
        try:
            d2 = json.load(open(p2))
            profiles = d2.get("profiles", {})
            for key in ("telegram:default", "telegram"):
                if key in profiles:
                    return profiles[key].get("token", "")
        except Exception:
            pass
    return ""


def _classify_sender(chat_id: int) -> SenderRole:
    if chat_id == RYAN_CHAT_ID:
        return "owner"
    if chat_id in (BETH_CHAT_ID,):
        return "teammate"
    return "other"


class TelegramChannelAdapter:
    name = "telegram"

    def __init__(self, bot_token: Optional[str] = None) -> None:
        self._token = bot_token or _load_bot_token()

    def ingest(self, raw_message: dict[str, Any]) -> Optional[InboundTaskRequest]:
        # Accept either a full Update dict {"update_id":..., "message":{...}}
        # or a bare message dict.
        msg = raw_message.get("message", raw_message)
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")
        message_id = msg.get("message_id")
        if not chat_id or not text or message_id is None:
            return None
        username = msg.get("from", {}).get("username") or str(chat_id)
        return InboundTaskRequest(
            channel="telegram",
            sender_identifier=f"{username}@telegram",
            sender_role=_classify_sender(int(chat_id)),
            intent=text,
            external_message_id=f"tg-{chat_id}-{message_id}",
            raw_payload={"channel": "telegram", "chat_id": chat_id, "message_id": message_id},
        )

    def send(self, channel: str, body: str, attachments: Optional[list] = None) -> None:
        if not self._token:
            return  # silent no-op if not configured
        chat_id = channel  # channel param is the chat_id (or symbolic like "#flyn-alerts" — Phase 1b)
        try:
            data = urllib.parse.urlencode({
                "chat_id": chat_id, "text": body, "parse_mode": "Markdown",
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                data=data, method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            return  # best-effort

    def approve_button(self, task_id: str, action: str) -> None:
        # MVP: just send a plain message describing the approval expected.
        # Real inline keyboard ships in Phase 1b.
        body = f"Approval needed for {task_id}: {action}\nReply '/approve {task_id}' to proceed."
        # No chat_id known here; caller must use send() with explicit chat_id.
        # Leave as no-op for the MVP; callers use send() directly.
        pass
