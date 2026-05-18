#!/usr/bin/env bash
# Daily flyn-orchestrator heartbeat. Phase 0 component: memory roll-up + hot decay.
# Phase 1 will add: prune-stale, cost-ledger-close, stale-PR-nudge.
set -euo pipefail

LOG_PREFIX="$(date -Iseconds) flyn-orchestrator-daily:"
echo "$LOG_PREFIX start"

# 1) Hot decay — POST to the router's maintenance endpoint
curl -sS -X POST http://127.0.0.1:8400/api/memory/maintenance/decay \
  -H 'Content-Type: application/json' \
  -d '{"sender_role":"owner"}' >/dev/null 2>&1 \
  && echo "$LOG_PREFIX hot decay completed" \
  || echo "$LOG_PREFIX hot decay endpoint unreachable"

# 2) Memory roll-up — summarize today's cool-tier events into one warm episode.
WS="${FLYN_WORKSPACE:-$HOME/.openclaw/workspace}"
TODAY="$(date -u +%Y-%m-%d)"
COOL_FILE="$WS/memory/orchestrator/$TODAY-cool-events.jsonl"

if [ -f "$COOL_FILE" ]; then
  COUNT=$(wc -l < "$COOL_FILE" | tr -d ' ')
  if [ "$COUNT" -gt 0 ]; then
    # Hard caps per spec §2.5: <=8 facts / <=2000 chars.
    SUMMARY=$(python3 -c "
import json
seen = set()
facts = []
with open('$COOL_FILE') as f:
    for line in f:
        try: e = json.loads(line)
        except: continue
        if e['subject'] in seen: continue
        seen.add(e['subject'])
        facts.append(f\"- {e['subject']} ({e['event_type']}): {e['body'][:160]}\")
        if len(facts) == 8: break
print('\n'.join(facts)[:2000])
")
    BODY=$(python3 -c "import sys; print(f'Daily cool-tier rollup for $TODAY ($COUNT events; top 8 distinct subjects):\n{sys.argv[1]}')" "$SUMMARY")
    PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'source':'orchestrator','event_type':'daily_rollup','subject':'rollup-$TODAY','body':sys.argv[1],'dedup_key':'rollup-$TODAY'}))" "$BODY")
    curl -sS -X POST http://127.0.0.1:8400/api/memory/ingest \
      -H 'Content-Type: application/json' \
      -d "$PAYLOAD" >/dev/null
    echo "$LOG_PREFIX rolled up $COUNT cool events"
  fi
else
  echo "$LOG_PREFIX no cool events for $TODAY (skip)"
fi

# Stale-PR nudge (Phase 2)
PR_NUDGE="$HOME/AI/openclaw/flyn-agent/deploy/orchestrator/bin/flyn-pr-nudge"
if [ -x "$PR_NUDGE" ]; then
  python3 "$PR_NUDGE" 2>&1 \
    || echo "$LOG_PREFIX flyn-pr-nudge exited non-zero (non-fatal)"
fi

# Phase 5b sweep — expire AWAITING_OWNER_APPROVAL tasks beyond their tier window.
# Endpoint returns {ok, transitioned: [...], count: N}. We log count; details
# go to memory events + audit_log via the orchestrator's emit hooks.
SWEEP_RESP=$(curl -sS --max-time 10 -X POST http://127.0.0.1:8300/api/maintenance/sweep-expired-approvals 2>/dev/null || echo "")
if [ -n "$SWEEP_RESP" ]; then
  COUNT=$(echo "$SWEEP_RESP" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("count", 0))
except Exception: print(0)' 2>/dev/null || echo 0)
  echo "$LOG_PREFIX approval sweep transitioned $COUNT task(s) to REJECTED"
else
  echo "$LOG_PREFIX approval sweep endpoint unreachable (orchestrator stopped?)"
fi

echo "$LOG_PREFIX done"
