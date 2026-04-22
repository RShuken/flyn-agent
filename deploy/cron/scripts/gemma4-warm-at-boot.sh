#!/usr/bin/env bash
# Fire a tiny gemma4:e4b request at boot so the first real call (hourly
# memory-autosave, flyn turn, etc.) doesn't pay the 10–15s cold-load tax.
# OLLAMA_KEEP_ALIVE=3h is set in homebrew.mxcl.ollama.plist, so once warm
# the model stays resident.
#
# Wait up to 3 minutes for Ollama to come online (covers staggered boot).

set -u
PULSE_NAME="gemma4-warm-at-boot"
source "$(dirname "$0")/common.sh"

log "start"

# Wait for Ollama.
for i in $(seq 1 90); do
  if curl -sSf --max-time 2 http://localhost:11434/ >/dev/null 2>&1; then
    log "ollama reachable after ${i} polls"
    break
  fi
  if [ "$i" = "90" ]; then
    log "ollama not reachable after 180s; giving up"
    exit 1
  fi
  sleep 2
done

# Fire a no-op prompt. keep_alive: 3h matches server default so we don't
# regress the pin on servers where env var hasn't been read.
resp="$(curl -sS --max-time 180 http://localhost:11434/api/generate \
  -d '{"model":"gemma4:e4b","prompt":"hi","stream":false,"keep_alive":"3h","options":{"num_predict":2}}' \
  2>&1)"
if echo "$resp" | grep -q '"done":true'; then
  load_ms="$(echo "$resp" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(round(d.get("load_duration",0)/1e6))')"
  log "gemma4 warm (load_duration_ms=${load_ms})"
else
  log "warm request failed: $(echo "$resp" | head -c 200)"
  exit 1
fi

log "done"
