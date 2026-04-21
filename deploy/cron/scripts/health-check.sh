#!/usr/bin/env bash
# Pulse: daily-health-check
# Runs daily 22:00 local per HEARTBEAT.md. Silent when green; alerts on failures.

PULSE_NAME="health-check"
source "$(dirname "$0")/common.sh"

log "start"
FAILURES=()

# 1. OpenClaw gateway health — connects to the gateway and returns channel/agent info
if openclaw health 2>&1 | grep -qE '(Telegram|Agents: main)'; then
  log "openclaw health: GREEN"
else
  FAILURES+=("openclaw gateway health check failed (no Telegram/Agents line)")
fi

# 2. Codex OAuth present — read auth-profiles.json directly (no `auth list` subcommand exists)
if python3 -c "import json; d=json.load(open('${AUTH_FILE}')); assert any(k.startswith('openai-codex') for k in d['profiles'])" 2>/dev/null; then
  log "codex auth: present"
else
  FAILURES+=("Codex OAuth profile missing from auth-profiles.json")
fi

# 2b. Codex OAuth not expired — check the expires field if present
if python3 -c "
import json, time
d=json.load(open('${AUTH_FILE}'))
for k,v in d['profiles'].items():
  if k.startswith('openai-codex') and 'expires' in v:
    exp = v['expires']
    # expires can be ms or seconds; if > year 2100 as ms, convert
    if exp > 10**12: exp = exp / 1000
    if time.time() > exp:
      raise SystemExit(1)
" 2>/dev/null; then
  log "codex auth: not expired"
else
  FAILURES+=("Codex OAuth token has expired — run: openclaw models auth login --provider openai-codex")
fi

# 3. Graphiti REST
if curl -sSf --max-time 10 "${KG_API}/api/health" | grep -q '"status":"ok"'; then
  log "graphiti REST: GREEN"
else
  FAILURES+=("graphiti REST (/api/health) not responding")
fi

# 4. Neo4j container
if docker ps --filter name=flyn-neo4j --format '{{.Names}}' | grep -q flyn-neo4j; then
  log "neo4j docker: running"
else
  FAILURES+=("flyn-neo4j Docker container not running")
fi

# 5. Gemma 4 model present
if ollama list 2>&1 | grep -q 'gemma4:e4b'; then
  log "gemma4:e4b: pulled"
else
  FAILURES+=("gemma4:e4b not in ollama list")
fi

# 6. Disk space on home
DISK_PCT="$(df -h "${HOME}" | tail -1 | awk '{print $5}' | tr -d '%')"
if [ "${DISK_PCT:-0}" -gt 85 ]; then
  FAILURES+=("disk usage on ${HOME} at ${DISK_PCT}% (threshold 85%)")
else
  log "disk: ${DISK_PCT}% used (OK)"
fi

# 7. Report
if [ "${#FAILURES[@]}" -eq 0 ]; then
  log "all checks GREEN; silent exit"
  exit 0
fi

# Alert on any failures
MSG="Flyn daily health check — ${#FAILURES[@]} failure(s):"
for f in "${FAILURES[@]}"; do MSG="${MSG}"$'\n'" - ${f}"; done
alert_telegram "$MSG"
log "ALERTED: ${#FAILURES[@]} failures"
exit 1
