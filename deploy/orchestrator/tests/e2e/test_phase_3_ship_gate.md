# Phase 3 Ship Gate — Manual E2E

**Spec §8 Phase 3 ship gate:** One research request → markdown report delivered with citations; critic clean; report used.

This playbook runs after Phase 3 merges to main. Requires the orchestrator on `:8300` deployed with Phase 3 code, `ANTHROPIC_API_KEY` (real `sk-ant-api03-*`) in env or auth-profiles, and a writable `~/Work/research/` directory (or `FLYN_RESEARCH_OUTPUT_ROOT` env override).

## Pre-conditions

```bash
# Services
curl -sS http://127.0.0.1:8400/api/health
curl -sS http://127.0.0.1:8300/api/health

# Auth (real API key, not OAuth token)
python3 -c "
import json
from pathlib import Path
d = json.load(open(Path.home() / '.openclaw/agents/main/agent/auth-profiles.json'))
t = d.get('profiles', {}).get('anthropic:default', {}).get('token', '')
print('API key prefix:', t[:10], '(must start with sk-ant-api)')
"

# Output dir writable
mkdir -p ~/Work/research && touch ~/Work/research/.gitkeep && ls -la ~/Work/research

# Clear state
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "DELETE FROM tasks; DELETE FROM task_events; UPDATE task_id_counter SET last=0;"
```

## Procedure

### Step 1: Send a research task

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "ryan@telegram",
    "sender_role": "owner",
    "intent": "research how to use PyYAML safely in Python — best practices for safe_load, common pitfalls, and current 2026 recommendations",
    "external_message_id": "p3-shipgate-1",
    "raw_payload": {"channel": "telegram", "chat_id": 7191564227}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
echo "TASK_ID=$TASK_ID"
```

Expected: `{"task_id":"T-0001","state":"inbound","accepted":true}`.

### Step 2: Watch state transitions

```bash
for i in $(seq 1 30); do
  sleep 20
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    deliverable_ready) echo "PASS: deliverable_ready"; break ;;
    changes_requested) echo "CRITIC BLOCKED — task transitioned to changes_requested (review the critic findings)"; break ;;
    failed|cost_paused|cancelled) echo "FAIL: $STATE"; break ;;
  esac
done
```

Expected: `triaging → routed → decomposed → dispatched → running → reviewed → deliverable_ready` in ~3-8 minutes (4 parallel researchers + critic + synthesizer).

### Step 3: Confirm the report exists

```bash
TASK_INFO=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID)
REPORT_PATH=$(echo "$TASK_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("raw_payload",{}).get("report_path","(none)"))')
echo "REPORT_PATH=$REPORT_PATH"
ls -la "$REPORT_PATH"
head -50 "$REPORT_PATH"
```

Expected: file exists at `~/Work/research/pyyaml-safely/<date>-pyyaml-safely.md` (or similar slug), Markdown report begins with `# Title`, `## Summary`, `## Findings`.

### Step 4: Confirm raw notes preserved

```bash
RAW_DIR="$(dirname "$REPORT_PATH")/raw"
ls "$RAW_DIR"
cat "$RAW_DIR"/*Q1*.json | python3 -m json.tool | head -30
```

Expected: 2-4 JSON files (one per sub-question) with `sub_question_id`, `answer`, `citations`, `confidence`.

### Step 5: Confirm citations are real

Open 2-3 citation URLs from the raw notes in a browser. Each must resolve to a real page that actually supports the cited claim.

```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('$RAW_DIR/*.json')):
    d = json.load(open(f))
    print(f'=== {d[\"sub_question_id\"]} ({d[\"confidence\"]}) ===')
    for c in d['citations']:
        print(f'  {c[\"url\"]}  -  {c[\"claim\"][:60]}')
"
```

### Step 6: Confirm critic ran clean

```bash
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "SELECT from_state, to_state, actor, reason FROM task_events WHERE task_id='$TASK_ID' ORDER BY id"
```

Expected: a `reviewed → deliverable_ready` row with `actor=router`. If you see `reviewed → changes_requested`, the critic blocked — read the audit_log + memory router for the `critique_complete` event to see the findings.

### Step 7: Confirm Telegram notify landed

Manually check Ryan's phone (chat_id 7191564227) for a Telegram message containing `✅ T-0001 delivered`, the report path, and a 500-char synthesis snippet.

### Step 8: Sign-off

- [ ] Steps 1-2: research task went from inbound → deliverable_ready
- [ ] Step 3: report file exists with proper Markdown structure
- [ ] Step 4: raw notes preserved (one JSON per sub-question)
- [ ] Step 5: citations are real, resolve to real pages, support the claims
- [ ] Step 6: critic verdict captured in audit_log
- [ ] Step 7: Telegram notify landed
- [ ] All 141 unit + integration tests still pass
- [ ] Ryan signs

Date: ____________  Ryan: ____________

## What this proves

If all 8 steps pass, Phase 3 is shipped per spec §8: a real research request decomposed into N parallel sub-questions, researchers found real sources, the critic audited for bias/contradiction/unsourced claims, and the synthesizer merged into a single delivered report.

## Failure modes

- **PM emits unparseable JSON**: task → FAILED. Re-check the pm_research.md prompt for clarity.
- **Researcher emits non-JSON or no citations**: that sub-question's output is dropped; if all drop, task → FAILED.
- **Critic blocks**: task → CHANGES_REQUESTED. Read the critic findings via the audit_log; for now Phase 3 doesn't auto-rerun (Phase 3b will add a "fix and re-critique" loop).
- **No real ANTHROPIC_API_KEY**: every worker fails OAuth, capture is 0 bytes, dispatcher's `WorkerProducedNothing` guard kicks in, task → FAILED.

## Deferred to Phase 3b

- Auto-rerun on critic block (re-prompt researchers to address findings)
- Cite-while-fetching (instead of post-hoc validation, capture URL during the WebFetch tool call)
- Per-source caching (don't re-fetch the same URL across sub-questions)
- Confidence aggregation (per-source-quality weighted)
