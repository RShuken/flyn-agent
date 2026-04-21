#!/usr/bin/env bash
# Pulse: morning-digest
# Runs weekdays 07:00 local per HEARTBEAT.md.
# Summarizes overnight activity + today's calendar and posts to Telegram #flyn-briefing.
#
# External integrations required for full functionality:
#   - gog (Google Workspace — Gmail/Calendar)    → install via brew tap steipete/packages && brew install gog
#   - Cora/Railway deploy status                  → set RAILWAY_TOKEN in auth-profiles.json (optional)
#
# Without those, this script still runs and reports what it CAN see
# (gateway health, cron failures from the last 24h).

PULSE_NAME="morning-digest"
source "$(dirname "$0")/common.sh"

log "start"

SECTIONS="$(mktemp)"
trap 'rm -f "$SECTIONS"' EXIT

# 1. Gateway + cron health (always available)
{
  echo "## Last 24 hours on 4C"
  echo
  # Cron run outcomes
  if openclaw cron runs --since 24h 2>/dev/null | tail -20 | grep -qE '(error|FAILED|fail)'; then
    echo "⚠️  Some cron runs failed in the last 24h:"
    openclaw cron runs --since 24h 2>/dev/null | grep -iE 'error|fail' | tail -5
    echo
  else
    echo "✓ All cron runs healthy (last 24h)"
    echo
  fi
  # Gateway errors
  ERRORS="$(tail -1000 "/tmp/openclaw/openclaw-$(date +%Y-%m-%d).log" 2>/dev/null | grep -iE '"logLevelName":"ERROR"' | wc -l | tr -d ' ')"
  echo "Gateway error log entries today: ${ERRORS:-0}"
} >> "$SECTIONS"

# 2. Graphiti KG activity in last 24h
{
  echo
  echo "## Knowledge graph last 24h"
  EP_JSON="$(curl -sS --max-time 10 "${KG_API}/api/episodes?limit=20" 2>/dev/null || echo '{}')"
  RECENT="$(echo "$EP_JSON" | python3 -c "import json,sys,datetime;d=json.load(sys.stdin); eps=d.get('episodes',[]); now=datetime.datetime.now(datetime.timezone.utc); cnt=sum(1 for e in eps if e.get('created_at') and (now - datetime.datetime.fromisoformat(e['created_at'].replace('Z','+00:00'))).total_seconds() < 86400); print(cnt)" 2>/dev/null || echo "?")"
  echo "Episodes ingested: ${RECENT}"
} >> "$SECTIONS"

# 3. Today's calendar (requires gog)
if command -v gog >/dev/null 2>&1; then
  {
    echo
    echo "## Today's calendar"
    gog calendar list --today 2>/dev/null | head -15 || echo "(gog calendar list failed)"
  } >> "$SECTIONS"
else
  echo "(gog not installed; skipping calendar)" >> "$SECTIONS"
  log "gog not installed; calendar section skipped"
fi

# 4. Unread email (requires gog)
if command -v gog >/dev/null 2>&1; then
  {
    echo
    echo "## Unread email (top senders)"
    gog gmail search 'is:unread newer_than:1d' --format summary 2>/dev/null | head -10 || echo "(gog gmail search failed)"
  } >> "$SECTIONS"
fi

# 5. Summarize via local gemma4 for a tight briefing
DIGEST="$(local_summarize 'Produce a morning briefing for Ryan based on this raw data. Lead with anything actionable. 4-8 bullets max. No filler. Use concrete counts from the data.' < "$SECTIONS" 2>/dev/null || cat "$SECTIONS")"

# 6. Post to Telegram #flyn-briefing
if openclaw channels send --channel telegram --target '#flyn-briefing' --message "$DIGEST" >/dev/null 2>&1; then
  log "posted morning digest to #flyn-briefing"
else
  log "channel send failed; digest saved to log instead"
  log "--- digest content ---"
  log "$DIGEST"
fi

log "done"
