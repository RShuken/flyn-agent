#!/usr/bin/env bash
# One-shot pulse: orchestrator-overnight-digest
# Fires once at the next scheduled time (set by ai.flyn.pulse.orchestrator-overnight-digest.plist).
# Posts a Telegram digest to Ryan summarizing what shipped on
# feat/orchestrator-foundation-phase-1 overnight.
set -euo pipefail

LOG_PREFIX="$(date -Iseconds) orchestrator-overnight-digest:"
echo "$LOG_PREFIX start"

REPO_ROOT="${FLYN_AGENT_ROOT:-$HOME/AI/openclaw/flyn-agent}"
WORKTREE="${FLYN_PHASE1_WORKTREE:-$HOME/AI/openclaw/flyn-agent-phase-1}"
PHASE1_BRANCH="${FLYN_PHASE1_BRANCH:-feat/orchestrator-foundation-phase-1}"
SINCE_DATE="${FLYN_DIGEST_SINCE:-2026-05-15 02:00}"

# Bot token + chat id
TOKEN=$(python3 -c "
import json, sys
from pathlib import Path
p = Path.home() / '.openclaw' / 'agents' / 'main' / 'agent' / 'auth-profiles.json'
d = json.load(open(p))
# auth-profiles structure differs from openclaw.json; try common paths
for key in ('telegram:default', 'telegram'):
    if key in d.get('profiles', {}):
        print(d['profiles'][key].get('token','')); sys.exit(0)
# fall back to openclaw.json channels.telegram.botToken
p2 = Path.home() / '.openclaw' / 'openclaw.json'
if p2.exists():
    d2 = json.load(open(p2))
    print(d2.get('channels',{}).get('telegram',{}).get('botToken',''))
" 2>/dev/null || true)

if [ -z "$TOKEN" ]; then
  echo "$LOG_PREFIX ERROR: no telegram bot token found"
  exit 1
fi

# Ryan's chat_id is 7191564227 per workspace/USER.md
RYAN_CHAT_ID="${FLYN_RYAN_CHAT_ID:-7191564227}"

# 1. Phase 1 git log since last night
PHASE1_COMMITS=""
if [ -d "$WORKTREE/.git" ] || [ -d "$WORKTREE" ]; then
  PHASE1_COMMITS=$(cd "$WORKTREE" && git log --oneline --since="$SINCE_DATE" "$PHASE1_BRANCH" 2>/dev/null | head -40 || echo "(no commits)")
fi
P1_COUNT=$(printf '%s' "$PHASE1_COMMITS" | grep -c . || true)

# 2. Phase 0 router health
ROUTER_HEALTH=$(curl -sS --max-time 5 http://127.0.0.1:8400/api/health 2>/dev/null || echo "(unreachable)")

# 3. Phase 1 orchestrator service status (it may not exist yet)
ORCH_HEALTH=$(curl -sS --max-time 5 http://127.0.0.1:8300/api/health 2>/dev/null || echo "(not deployed yet)")

# 4. Test count on Phase 1 branch
TEST_COUNT="(orchestrator package not yet present)"
if [ -d "$WORKTREE/deploy/orchestrator/.venv" ]; then
  TEST_COUNT=$(cd "$WORKTREE/deploy/orchestrator" && .venv/bin/python -m pytest tests/ --co -q 2>/dev/null | tail -1 || echo "(pytest run failed)")
fi

# 5. Rubric snapshot
RUBRIC_LINE=$(grep "^| \*\*1 — Orchestrator" "$WORKTREE/deploy/outcomes/ORCHESTRATOR-PHASE-RUBRIC.md" 2>/dev/null | head -1 || echo "(rubric not found)")

# Compose the message as PLAIN TEXT (no parse_mode).
# Reason: this digest embeds raw `git log` output, which can contain any chars
# valid in a commit message (underscores, asterisks, backticks, brackets,
# parens) — those reliably break Telegram Markdown v1 entity parsing with
# "Bad Request: can't parse entities". Failing daily is worse than losing
# bold/code styling; we send plain text instead.
MSG=$(cat <<EOF
Flyn orchestrator — overnight digest

Phase 1 (orchestrator foundation, MVP plan)
Commits since $SINCE_DATE: $P1_COUNT
$([ -n "$PHASE1_COMMITS" ] && printf '%s' "$PHASE1_COMMITS" || echo '(none)')

Service health
• Phase 0 router (:8400): $(echo "$ROUTER_HEALTH" | head -c 80)
• Phase 1 orchestrator (:8300): $(echo "$ORCH_HEALTH" | head -c 80)

Tests
$TEST_COUNT

Rubric snapshot
$RUBRIC_LINE

Next steps for you
1. Run the Phase 0 manual ship-gate playbook on your phone (real Telegram DM step)
2. Review the Phase 1 MVP plan at docs/superpowers/plans/2026-05-15-flyn-orchestrator-phase-1-mvp.md on the feat/orchestrator-foundation-phase-1 branch
3. Resume the build from wherever the overnight run left off — auto-memory has details

PR #1 (Phase 0): https://github.com/RShuken/flyn-agent/pull/1 (merged)
Phase 1 branch: https://github.com/RShuken/flyn-agent/tree/$PHASE1_BRANCH
EOF
)

# Send to Telegram as plain text. Capture HTTP status so we can detect future
# failures even though we no longer use parse_mode.
RESPONSE=$(curl -sS -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -d "chat_id=${RYAN_CHAT_ID}" \
  --data-urlencode "text=${MSG}")
echo "$RESPONSE" | head -c 200
echo

# If Telegram returned ok:false, surface a clear log line so the next pulse
# run is easy to triage.
if printf '%s' "$RESPONSE" | grep -q '"ok":false'; then
  echo "$LOG_PREFIX ERROR: Telegram rejected message: $(printf '%s' "$RESPONSE" | head -c 300)"
  exit 1
fi

echo "$LOG_PREFIX done"
