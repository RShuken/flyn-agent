#!/usr/bin/env bash
# Pulse: hourly-memory-save
# Rolls up the last hour of session activity into markdown + Graphiti.
# Runs at top of every hour 06:00-23:00 local per HEARTBEAT.md.

PULSE_NAME="memory-autosave"
source "$(dirname "$0")/common.sh"

DATE="$(date +%Y-%m-%d)"
HOUR="$(date +%H)"
DAY_FILE="${MEMORY_DIR}/${DATE}.md"
NOW_ISO="$(date '+%Y-%m-%dT%H:%M:%S%z')"

log "start"

# 1. Build a raw rollup of the last hour's activity from session logs + recent files.
#    OpenClaw native: openclaw memory status gives index state.
HOUR_SUMMARY_INPUT="$(mktemp)"
trap 'rm -f "$HOUR_SUMMARY_INPUT"' EXIT

{
  echo "=== recent session activity (last 60 min) ==="
  # Latest sessions list
  openclaw sessions list --limit 5 2>/dev/null | head -30 || true
  echo
  echo "=== recent workspace/memory diffs ==="
  find "${MEMORY_DIR}" -maxdepth 1 -type f -name '*.md' -mmin -70 -print -exec tail -80 {} \; 2>/dev/null || true
  echo
  echo "=== recent gateway log highlights ==="
  tail -200 /tmp/openclaw/openclaw-${DATE}.log 2>/dev/null | grep -iE '(error|heartbeat|promote|fallback)' | tail -30 || true
} > "$HOUR_SUMMARY_INPUT"

# 2. Summarize via local gemma4:e4b.
ROLLUP="$(local_summarize 'Summarize the last hour of Flyn activity as 3-6 short bullets. Each bullet: what happened, what changed, what to remember.' < "$HOUR_SUMMARY_INPUT" 2>/dev/null || echo '(local summarize failed; empty rollup)')"

if [ -z "$ROLLUP" ] || [ "$ROLLUP" = "(local summarize failed; empty rollup)" ]; then
  log "no rollup produced; skipping writes"
  exit 0
fi

# 3. Append to daily markdown.
{
  printf '\n## %s — hourly rollup (%s:00)\n\n' "$NOW_ISO" "$HOUR"
  printf '%s\n' "$ROLLUP"
} >> "$DAY_FILE"
log "markdown appended to ${DAY_FILE}"

# 4. POST the same rollup to Graphiti so typed facts land in Neo4j.
EPISODE_NAME="hourly-rollup-${DATE}-${HOUR}"
EPISODE_BODY="Hourly activity rollup for Flyn on ${NOW_ISO}:\n\n${ROLLUP}"
if kg_add_episode "$EPISODE_NAME" "$EPISODE_BODY"; then
  log "graphiti ingest OK"
else
  log "graphiti ingest FAILED (markdown still written)"
  # Don't alert on a single miss; alert threshold is handled by the weekly rollup diff.
fi

log "done"
