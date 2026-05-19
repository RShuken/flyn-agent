#!/usr/bin/env bash
# OpenClaw internal hook: forwards inbound Telegram messages to memory-router.
#
# Triggered on inbound messages. Reads the message JSON on stdin, builds a
# conversation_message InboundEvent payload, POSTs to localhost:8400. If the
# memory-router is down or returns non-200, logs to /tmp/flyn-conv-memory-tap.log
# and returns 0 — never blocks openclaw's message processing.

set -uo pipefail

LOG=/tmp/flyn-conv-memory-tap.log
ROUTER_URL="${FLYN_MEMORY_ROUTER_URL:-http://localhost:8400}"

# Read message JSON from stdin
read -r MSG_JSON

# Extract fields with python (jq may not be on PATH from openclaw's launchd context)
PAYLOAD=$(MSG_JSON="$MSG_JSON" python3 -c '
import json, os, datetime, sys
m = json.loads(os.environ["MSG_JSON"])
channel = m.get("channel", "telegram")
chat_id = m.get("chat_id") or m.get("chat", {}).get("id", 0)
sender_id = m.get("sender_id") or m.get("from", {}).get("id", 0)
msg_id = m.get("message_id") or m.get("id", 0)
text = m.get("text") or m.get("body", "")
ts = m.get("ts") or datetime.datetime.now(datetime.timezone.utc).isoformat()
out = {
  "source": "telegram",
  "event_type": "conversation_message",
  "subject": f"tg-{chat_id}-{msg_id}",
  "body": text,
  "importance": "warm",
  "raw_payload": {
    "channel": channel,
    "chat_id": chat_id,
    "sender_id": sender_id,
    "thread_id": chat_id,
    "reply_to_msg_id": m.get("reply_to_message", {}).get("message_id"),
    "attachments": m.get("attachments", []),
    "ts": ts,
  },
  "dedup_key": f"tg-{chat_id}-{msg_id}",
}
print(json.dumps(out))
')

curl -sS -m 3 -X POST "$ROUTER_URL/api/memory/ingest" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  > /tmp/.flyn-conv-tap-last 2>>"$LOG" \
  || echo "$(date -Iseconds) tap: POST failed (router down?)" >> "$LOG"

exit 0
