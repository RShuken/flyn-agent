#!/usr/bin/env bash
# Pulse: meeting-categorize
# Runs nightly at 02:30 to route pending Krisp meetings.

PULSE_NAME="meeting-categorize"
source "$(dirname "$0")/common.sh"

log "start"

PY=/Users/4c/AI/flyn-agent/deploy/wiki-backend/.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi

cd /Users/4c/AI/flyn-agent/deploy/pm
OUTPUT="$("$PY" meeting_categorizer.py 2>&1)" || {
  log "categorizer exited non-zero: $OUTPUT"
  alert_telegram "categorizer failed: ${OUTPUT:0:200}"
  exit 1
}
log "result: $OUTPUT"

# If any meetings are now in 'review', ping #flyn-briefing with the count.
REVIEW_COUNT="$(sqlite3 ~/.openclaw/data/flyn-meetings.db \
  "SELECT COUNT(*) FROM meetings WHERE status='review'" 2>/dev/null || echo 0)"
if [ "${REVIEW_COUNT:-0}" -gt 0 ]; then
  openclaw channels send --channel telegram --target '#flyn-briefing' \
    --message "🎤 ${REVIEW_COUNT} meeting(s) need routing — see morning digest for /route commands." \
    >/dev/null 2>&1 || log "channel send failed"
fi

log "done"
