#!/usr/bin/env bash
# Shared helpers for Flyn's cron scripts.

set -euo pipefail

FLYN_HOME="${HOME}"
OC_ROOT="${FLYN_HOME}/.openclaw"
AUTH_FILE="${OC_ROOT}/agents/main/agent/auth-profiles.json"
LOG_DIR="${OC_ROOT}/logs"
MEMORY_DIR="${OC_ROOT}/workspace/memory"
KG_API="http://localhost:8100"

mkdir -p "${LOG_DIR}" "${MEMORY_DIR}" "${MEMORY_DIR}/weekly"

# Path so cron has access to brew/docker/openclaw/ollama.
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

log() {
  local pulse="${PULSE_NAME:-unknown}"
  local ts="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf '%s  [%s]  %s\n' "$ts" "$pulse" "$*" >> "${LOG_DIR}/heartbeat-${pulse}-$(date +%Y-%m-%d).log"
}

alert_telegram() {
  local msg="$1"
  local pulse="${PULSE_NAME:-flyn}"
  log "ALERT → #flyn-alerts: ${msg}"
  # Use OpenClaw channels to avoid embedding bot tokens in cron scripts.
  openclaw channels send --channel telegram --target '#flyn-alerts' \
    --message "[${pulse}] ${msg}" >/dev/null 2>&1 || log "(channels send failed; alert only logged)"
}

# Post an episode to Flyn's Graphiti KG.
#   kg_add_episode <name> <episode_body>
kg_add_episode() {
  local name="$1"
  local body="$2"
  local resp
  resp="$(curl -sS --max-time 600 -X POST "${KG_API}/api/episode" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; n,b=sys.argv[1:]; print(json.dumps({"name":n,"body":b}))' "$name" "$body")" \
    2>&1)" || { log "kg_add_episode curl failed: $resp"; return 1; }
  if echo "$resp" | grep -q '"ok":true'; then
    log "kg_add_episode OK: $name"
    return 0
  else
    log "kg_add_episode NOT OK: $resp"
    return 1
  fi
}

# Call local gemma4:e4b for a short summarization. Stdin → stdout.
local_summarize() {
  local prompt="${1:-Summarize the following in 2-3 sentences, plain prose:}"
  local input
  input="$(cat)"
  # Ollama /api/chat endpoint; no stream, non-tool-carrying (reliable for gemma4)
  python3 - "$prompt" "$input" <<'PY'
import json, sys, urllib.request
prompt = sys.argv[1]
body = sys.argv[2]
payload = {
  "model": "gemma4:e4b",
  "messages": [
    {"role":"system","content":"You produce concise factual summaries. No filler."},
    {"role":"user","content": f"{prompt}\n\n{body}"}
  ],
  "stream": False
}
req = urllib.request.Request(
  "http://localhost:11434/api/chat",
  data=json.dumps(payload).encode(),
  headers={"Content-Type":"application/json"}
)
with urllib.request.urlopen(req, timeout=180) as r:
  j = json.loads(r.read())
print(j.get("message",{}).get("content","").strip())
PY
}
