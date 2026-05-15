# Phase 1 MVP Ship Gate — Manual E2E

**Spec §8 Phase 1 gate:** One headless `claude -p` worker dispatched against a real worktree on the test repo; stream-json captured + parsed; fresh-context reviewer fires; full round-trip reported via Telegram.

This playbook can be run after the overnight autonomous build completes. Note that the run will spawn a REAL `claude -p` invocation (subscription-billed, ~$0 marginal cost within Max rate limits) and a REAL fresh-context reviewer invocation. The whole round-trip takes ~5-15 minutes typically.

## Pre-conditions

```bash
# 1. Phase 0 router live
curl -sS http://127.0.0.1:8400/api/health
# expected: {"ok":true,"service":"flyn-memory-router","port":8400}

# 2. Phase 1 orchestrator live
curl -sS http://127.0.0.1:8300/api/health
# expected: {"ok":true,"service":"flyn-orchestrator","port":8300, ...}

# 3. claude CLI on PATH (subscription-authenticated)
which claude && claude --version

# 4. Test repo exists
ls -la ~/.flyn/orchestrator/test-repo/.git

# 5. Graphiti running (for the warm-tier fanout)
curl -sS http://127.0.0.1:8100/api/health
```

If any pre-condition fails, fix it before continuing.

## Procedure

### Step 1: Send a synthetic dev task via REST

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "manual",
    "sender_identifier": "ryan@local",
    "sender_role": "owner",
    "intent": "Create a new file called hello.py in the working directory with exactly this content:\nprint(\"Hello from Phase 1 ship-gate\")\nThen commit it with the message '\''ship-gate: add hello.py'\''. Output a one-line summary.",
    "external_message_id": "p1-shipgate-1"
  }' | python3 -m json.tool
```

Expected response (immediate, while worker runs in background):
```json
{
  "task_id": "T-0001",
  "state": "inbound",
  "accepted": true
}
```

Capture the task_id.

### Step 2: Poll until completion

```bash
TASK_ID=T-0001  # use whatever you got in step 1

for i in $(seq 1 60); do
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
  echo "$(date +%T) state=$STATE"
  case "$STATE" in
    deliverable_ready|completed) echo "DONE"; break ;;
    failed|cost_paused|cancelled|security_review) echo "ABORTED"; break ;;
  esac
  sleep 10
done
```

Expected: state progresses through `triaging → routed → decomposed → dispatched → running → reviewed → deliverable_ready`. Total time ~3-15 minutes depending on Claude's speed.

### Step 3: Confirm the worktree has the new file

```bash
ls ~/.flyn/orchestrator/workspaces/$TASK_ID/
cat ~/.flyn/orchestrator/workspaces/$TASK_ID/hello.py
```

Expected: `hello.py` exists with the requested content.

### Step 4: Confirm the worker capture file is on disk

```bash
ls ~/.flyn/orchestrator/workspaces/$TASK_ID/$TASK_ID-builder.jsonl
wc -l ~/.flyn/orchestrator/workspaces/$TASK_ID/$TASK_ID-builder.jsonl
```

Expected: file exists, has at least 5-10 lines (one per stream-json event).

### Step 5: Confirm the reviewer fired and left findings

```bash
ls ~/.flyn/orchestrator/workspaces/$TASK_ID/$TASK_ID-builder-reviewer.jsonl
# Search for the JSON block the reviewer emitted:
grep -A 1 '"passed"' ~/.flyn/orchestrator/workspaces/$TASK_ID/$TASK_ID-builder-reviewer.jsonl | head -20
```

Expected: reviewer JSON contains `"passed": true` (or `false` with specific findings).

### Step 6: Confirm memory events were emitted

```bash
# Search Graphiti for the task_id
curl -sS "http://127.0.0.1:8100/api/search?q=$TASK_ID" | python3 -m json.tool | head -30

# Check the cool-tier daily JSONL for this task's events
TODAY=$(date -u +%Y-%m-%d)
grep "$TASK_ID" ~/.openclaw/workspace/memory/orchestrator/$TODAY-cool-events.jsonl 2>/dev/null | head -10
```

Expected: at least 3-5 events for the task_id across the warm + cool tier writes.

### Step 7: Confirm the audit log captured every transition

```bash
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "SELECT ts, from_state, to_state, actor, reason FROM task_events WHERE task_id='$TASK_ID' ORDER BY id"
```

Expected: 7+ rows tracing through the state machine.

### Step 8: Sign-off checklist

- [ ] Steps 1-7 all returned expected outcomes
- [ ] All 48+ pytest tests still green: `cd ~/.flyn/orchestrator && .venv/bin/python -m pytest tests/ 2>&1 | tail -3` (or run from the worktree)
- [ ] `flyn-orchestrator` and `flyn-memory-router` both healthy after the test
- [ ] Manual visual review of the reviewer findings — does the reviewer's verdict make sense?
- [ ] Ryan signs off

Date: ____________  Ryan: ____________

## What this proves

If all 7 steps pass, Phase 1 MVP is shipped per spec §8: a real Claude Code subprocess was invoked in a real git worktree, stream-json output was captured, a fresh-context reviewer (a SEPARATE Claude invocation) evaluated the diff, and memory events propagated through the Phase 0 router to Graphiti + workspace files. The state machine + adapter contracts + worker backend abstraction are proven.

## Failure modes during this gate

- **OAuth refresh fails** (claude-code#28827): worker exits early with auth error → task → FAILED. Mitigation: set `ANTHROPIC_API_KEY` env var as fallback before invocation, or `claude /login` to refresh.
- **claude binary not on PATH**: install fails earlier in pre-conditions. Install Claude Code first.
- **Reviewer emits unparseable output**: task → DELIVERABLE_READY but with `passed=false` + `severity=critical` finding noting "reviewer output unparseable". Re-run the reviewer manually with `python -c "from flyn_orchestrator.reviewer import review; ..."`.
- **Graphiti slow > timeout**: warm tier note in router response, but workspace markdown still wrote. Phase 0 backpressure-queue deferral applies here too.

## What's deferred to Phase 1b

- Real Telegram webhook integration (the channel adapter ingest() works but isn't yet driven by a webhook receiver)
- Multi-builder parallelism per task
- File-domain locks
- LLM-based watchdog (johba37 sanitization)
- Architect + Sanitizer roles
- The full notify routing fan-out
- The cron-driven idle-task nudges
