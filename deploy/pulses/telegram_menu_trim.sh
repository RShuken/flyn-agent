#!/usr/bin/env bash
# Trim Telegram bot menu to ~15 essential commands.
#
# Why: OpenClaw's gateway pushes all 89 registered slash commands to Telegram's
# setMyCommands API on every restart. The payload exceeds Telegram's
# conservative 5700-char menu budget and the gateway truncates descriptions,
# which can race with the health-monitor's 5-minute restart loop. Trimming
# the menu to essentials keeps the bot UI sane and removes a flake source.
#
# This pulse re-applies the trimmed menu every 10 minutes via launchd because
# the openclaw gateway re-pushes the full 89 every time it restarts (and the
# health-monitor restarts it every ~5 min). When openclaw upstream adds a
# native menu-restriction config, this can be retired.
set -euo pipefail

CONFIG="$HOME/.openclaw/openclaw.json"
LOG_PREFIX="$(date -Iseconds) telegram-menu-trim:"

if [ ! -f "$CONFIG" ]; then
  echo "$LOG_PREFIX no openclaw.json — skipping"
  exit 0
fi

TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG'))['channels']['telegram']['botToken'])" 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "$LOG_PREFIX no Telegram botToken in config — skipping"
  exit 0
fi

# Curated 15-command essential menu. Keep this list short and aligned with how
# Ryan + teammates actually use the bot. Reorder if the most-used commands
# change. Telegram displays these in order in the in-app menu.
PAYLOAD='{"commands":[
  {"command":"help","description":"Show available commands"},
  {"command":"status","description":"Show current status"},
  {"command":"tasks","description":"List background tasks"},
  {"command":"approve","description":"Approve or deny exec requests"},
  {"command":"send","description":"Send a message to another channel"},
  {"command":"focus","description":"Focus on a specific task"},
  {"command":"unfocus","description":"Clear current focus"},
  {"command":"new","description":"Start a new session"},
  {"command":"reset","description":"Reset current session"},
  {"command":"stop","description":"Stop current task"},
  {"command":"kill","description":"Kill all running tasks"},
  {"command":"compact","description":"Compact session memory"},
  {"command":"healthcheck","description":"Run system healthcheck"},
  {"command":"model","description":"Switch model"},
  {"command":"think","description":"Toggle extended thinking"}
]}'

# Check current command count; only re-apply if openclaw has bloated it.
CURRENT=$(curl -fsS --max-time 5 "https://api.telegram.org/bot$TOKEN/getMyCommands" \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('result',[])))" 2>/dev/null || echo "0")

if [ "$CURRENT" = "15" ]; then
  echo "$LOG_PREFIX already trimmed to 15 commands — noop"
  exit 0
fi

RESP=$(curl -fsS --max-time 10 -X POST "https://api.telegram.org/bot$TOKEN/setMyCommands" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" 2>&1) || {
  echo "$LOG_PREFIX setMyCommands failed: $RESP"
  exit 1
}

OK=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null || echo "false")
if [ "$OK" = "True" ]; then
  echo "$LOG_PREFIX trimmed menu from $CURRENT to 15 commands"
else
  echo "$LOG_PREFIX setMyCommands returned non-ok: $RESP"
  exit 1
fi
