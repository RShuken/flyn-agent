"""Tiny webhook → Telegram bridge.

Receives wiki backend webhook POSTs on /hook and DMs the relevant Flyn
operator via the openclaw Telegram bot. Verifies HMAC signature.

Routes (per the rubric — only operator-facing events here):
  decision.created      → DM Beth (PM)
  question.answered     → DM Beth + Ryan (audit-light)
  question.reassigned   → DM Beth

Future: per-stakeholder routing rules + thread/topic mapping. For now,
all messages go to Beth's chat_id (operator); Ryan gets a copy on
question.answered only.

Config (env or auth-profiles.json):
  OL_BRIDGE_PORT          default 8201
  OL_BRIDGE_SECRET        HMAC shared secret with the backend
  TELEGRAM_BOT_TOKEN      from openclaw config
  BETH_CHAT_ID            7434192034 (default — pulled from config.yaml)
  RYAN_CHAT_ID            7191564227

Run:
  uvicorn telegram_bridge:app --host 127.0.0.1 --port 8201
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request


BETH_CHAT_ID = os.environ.get("BETH_CHAT_ID", "7434192034")
RYAN_CHAT_ID = os.environ.get("RYAN_CHAT_ID", "7191564227")
SECRET = os.environ.get("OL_BRIDGE_SECRET", "")


def _load_bot_token() -> str:
    if v := os.environ.get("TELEGRAM_BOT_TOKEN"):
        return v
    # Pull from openclaw config
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    try:
        return json.loads(cfg_path.read_text())["channels"]["telegram"]["botToken"]
    except Exception:
        return ""


BOT_TOKEN = _load_bot_token()
app = FastAPI(title="OL Wiki → Telegram Bridge", version="0.1.0")


def _send_telegram(chat_id: str, text: str) -> int:
    if not BOT_TOKEN:
        return 0
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=json.dumps({"chat_id": int(chat_id), "text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except Exception:
        return 0


def _format(event: str, data: dict[str, Any]) -> str:
    if event == "decision.created":
        return (
            f"📝 New decision logged (#{data.get('decision_id')})\n"
            f"  {data.get('summary')}\n"
            f"  resolves: {', '.join(data.get('question_ids', []) or ['—'])}\n"
            f"  source: {data.get('source_meeting') or '—'}"
        )
    if event == "question.answered":
        ans = (data.get("answer_text") or "")
        ans_short = ans if len(ans) < 200 else ans[:197] + "…"
        return (
            f"✅ Q {data.get('question_id')} answered by {data.get('answered_by')}\n"
            f"  {ans_short}"
        )
    if event == "question.reassigned":
        return (
            f"🔁 Q {data.get('question_id')} reassigned\n"
            f"  {data.get('from')} → {data.get('to')}\n"
            f"  reason: {data.get('reason') or '—'}"
        )
    return f"(event {event}): {json.dumps(data)[:200]}"


@app.post("/hook")
async def receive_hook(
    request: Request,
    x_ol_webhook_signature: str | None = Header(None),
) -> dict[str, Any]:
    raw = await request.body()
    # HMAC verify if secret is configured
    if SECRET:
        if not x_ol_webhook_signature:
            raise HTTPException(status_code=401, detail="missing signature")
        expected = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_ol_webhook_signature):
            raise HTTPException(status_code=401, detail="signature mismatch")

    payload = json.loads(raw)
    event = payload.get("event", "unknown")
    data = payload.get("data", {})
    text = _format(event, data)

    delivered: list[str] = []
    # Routing: Beth always; Ryan on question.answered
    if BETH_CHAT_ID:
        if _send_telegram(BETH_CHAT_ID, text) == 200:
            delivered.append("beth")
    if event == "question.answered" and RYAN_CHAT_ID:
        if _send_telegram(RYAN_CHAT_ID, text) == 200:
            delivered.append("ryan")
    return {"ok": True, "event": event, "delivered_to": delivered}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "bot_token_present": bool(BOT_TOKEN),
            "beth_chat_id_present": bool(BETH_CHAT_ID)}
