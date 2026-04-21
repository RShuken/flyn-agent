#!/usr/bin/env bash
# Pulse: weekly-model-drift
# Runs Sundays 21:00 local per HEARTBEAT.md.
# Snapshots openclaw's model catalog + Ollama model list, diffs against last week,
# alerts if a model moved to Unknown or disappeared.

PULSE_NAME="model-drift"
source "$(dirname "$0")/common.sh"

SNAP_DIR="${LOG_DIR}/model-snapshots"
mkdir -p "$SNAP_DIR"
TODAY="$(date +%Y-%m-%d)"
NEW_SNAP="${SNAP_DIR}/${TODAY}.txt"

log "start"

{
  echo "--- openclaw models list ---"
  openclaw models list 2>&1 | sort || true
  echo
  echo "--- openclaw capability embedding providers ---"
  openclaw capability embedding providers 2>&1 | sort || true
  echo
  echo "--- ollama list ---"
  ollama list 2>&1 || true
} > "$NEW_SNAP"

# Find the most recent prior snapshot
PREV_SNAP="$(ls "${SNAP_DIR}"/*.txt 2>/dev/null | grep -v "${TODAY}.txt" | sort | tail -1 || echo '')"

if [ -z "$PREV_SNAP" ]; then
  log "first snapshot (no prior comparison); done"
  exit 0
fi

DIFF="$(diff "$PREV_SNAP" "$NEW_SNAP" || true)"
if [ -z "$DIFF" ]; then
  log "no drift since $(basename "$PREV_SNAP")"
  exit 0
fi

# Save the diff
DIFF_FILE="${SNAP_DIR}/drift-${TODAY}.diff"
echo "$DIFF" > "$DIFF_FILE"
log "drift detected vs $(basename "$PREV_SNAP"); diff saved at $DIFF_FILE"

# Alert if critical change detected (model → Unknown, provider → not configured, etc.)
if echo "$DIFF" | grep -qiE 'Unknown|not configured|deprecated|shutdown'; then
  alert_telegram "Model drift detected — critical strings in diff. See ${DIFF_FILE}"
else
  log "drift is benign (no Unknown/deprecated strings); no alert"
fi
