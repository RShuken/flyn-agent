#!/usr/bin/env bash
# Pulse: weekly-memory-rollup
# Runs Sundays 20:00 local per HEARTBEAT.md.
# Reads last 7 days of daily memory files, produces a weekly rollup,
# posts a consolidated episode to Graphiti, trims files older than 30 days
# to cold tier.

PULSE_NAME="memory-rollup"
source "$(dirname "$0")/common.sh"

WEEK_NUM="$(date +%Y-%V)"
ROLLUP_FILE="${MEMORY_DIR}/weekly/${WEEK_NUM}.md"

log "start (week ${WEEK_NUM})"

# 1. Concatenate last 7 days of daily memory files.
WEEK_INPUT="$(mktemp)"
trap 'rm -f "$WEEK_INPUT"' EXIT

for i in 0 1 2 3 4 5 6; do
  DATE="$(date -v-${i}d +%Y-%m-%d)"
  FILE="${MEMORY_DIR}/${DATE}.md"
  if [ -f "$FILE" ]; then
    echo "--- ${DATE} ---" >> "$WEEK_INPUT"
    cat "$FILE" >> "$WEEK_INPUT"
    echo >> "$WEEK_INPUT"
  fi
done

if [ ! -s "$WEEK_INPUT" ]; then
  log "no daily files in last 7 days; skipping"
  exit 0
fi

# 2. Summarize the week via local gemma4:e4b.
ROLLUP="$(local_summarize 'Produce a weekly rollup: 5-10 bullets covering key decisions, configuration changes, completed work, and unresolved items from this week. Group by project where relevant.' < "$WEEK_INPUT" 2>/dev/null || echo '')"

if [ -z "$ROLLUP" ]; then
  log "local_summarize failed; aborting (no markdown written, no graphiti post)"
  alert_telegram "weekly memory rollup: local_summarize returned empty; rollup NOT produced"
  exit 1
fi

# 3. Write the weekly rollup file.
{
  printf '# Weekly rollup — %s\n\n' "$WEEK_NUM"
  printf 'Generated %s by flyn memory-rollup pulse.\n\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf '## Summary\n\n%s\n' "$ROLLUP"
} > "$ROLLUP_FILE"
log "wrote weekly rollup to $ROLLUP_FILE"

# 4. Ingest the weekly rollup as one Graphiti episode.
kg_add_episode "weekly-rollup-${WEEK_NUM}" "Weekly rollup for Flyn, week ${WEEK_NUM}: ${ROLLUP}" \
  && log "graphiti ingest OK" \
  || log "graphiti ingest FAILED"

# 5. Trim daily files older than 30 days to a cold archive.
COLD_DIR="${MEMORY_DIR}/cold"
mkdir -p "$COLD_DIR"
find "${MEMORY_DIR}" -maxdepth 1 -type f -name '20*.md' -mtime +30 -print | while read -r old; do
  mv "$old" "$COLD_DIR/"
  log "moved $(basename "$old") to cold tier"
done

log "done"
