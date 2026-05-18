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

# Per-project forum topic cache: slug -> message_thread_id
_TOPIC_CACHE_PATH = Path.home() / ".flyn" / "orchestrator" / "telegram_topics.json"


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

    def __init__(
        self,
        bot_token: Optional[str] = None,
        memory_emitter: Optional[Any] = None,
    ) -> None:
        self._token = bot_token or _load_bot_token()
        self._memory_emitter = memory_emitter

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
        thread_id = msg.get("message_thread_id")
        raw_payload: dict[str, Any] = {"channel": "telegram", "chat_id": chat_id, "message_id": message_id}
        if thread_id is not None:
            raw_payload["thread_id"] = thread_id
        return InboundTaskRequest(
            channel="telegram",
            sender_identifier=f"{username}@telegram",
            sender_role=_classify_sender(int(chat_id)),
            intent=text,
            external_message_id=f"tg-{chat_id}-{message_id}",
            raw_payload=raw_payload,
        )

    def _load_topic_cache(self) -> dict[str, int]:
        """Read slug -> thread_id mapping from disk; returns {} if missing/corrupt."""
        try:
            return json.loads(_TOPIC_CACHE_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_topic(self, slug: str, thread_id: int) -> None:
        cache = self._load_topic_cache()
        cache[slug] = thread_id
        _TOPIC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOPIC_CACHE_PATH.write_text(json.dumps(cache, indent=2))

    def _get_topic_thread_id(self, slug: str) -> Optional[int]:
        return self._load_topic_cache().get(slug)

    def _create_forum_topic(self, chat_id: str, name: str) -> Optional[int]:
        """Call Telegram Bot API createForumTopic. Returns message_thread_id on success."""
        if not self._token:
            return None
        try:
            data = urllib.parse.urlencode({
                "chat_id": chat_id, "name": name,
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self._token}/createForumTopic",
                data=data, method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
            result = json.loads(body)
            if result.get("ok"):
                return result["result"]["message_thread_id"]
        except Exception as e:
            from .._observability import emit_swallowed_error
            emit_swallowed_error(self._memory_emitter, self.name, "create_forum_topic", e)
        return None

    def send(self, channel: str, body: str, attachments: Optional[list] = None,
             project_slug: Optional[str] = None) -> None:
        if not self._token:
            return  # silent no-op if not configured
        chat_id = channel  # channel param is the chat_id (or symbolic like "#flyn-alerts" — Phase 1b)
        thread_id = None
        if project_slug:
            thread_id = self._get_topic_thread_id(project_slug)
            if thread_id is None:
                # Create the topic, then cache and use the new thread_id
                new_id = self._create_forum_topic(chat_id, f"dev-{project_slug}")
                if new_id is not None:
                    self._save_topic(project_slug, new_id)
                    thread_id = new_id
        try:
            fields: dict[str, Any] = {
                "chat_id": chat_id, "text": body, "parse_mode": "Markdown",
            }
            if thread_id is not None:
                fields["message_thread_id"] = thread_id
            data = urllib.parse.urlencode(fields).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                data=data, method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            from .._observability import emit_swallowed_error
            emit_swallowed_error(self._memory_emitter, self.name, "send", e)
            return  # best-effort

    def approve_button(self, task_id: str, action: str) -> None:
        # MVP: just send a plain message describing the approval expected.
        # Real inline keyboard ships in Phase 1b.
        body = f"Approval needed for {task_id}: {action}\nReply '/approve {task_id}' to proceed."
        # No chat_id known here; caller must use send() with explicit chat_id.
        # Leave as no-op for the MVP; callers use send() directly.
        pass
