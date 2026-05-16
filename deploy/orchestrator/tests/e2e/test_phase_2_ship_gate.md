# Phase 2 Ship Gate — Manual E2E

**Spec §8 Phase 2 ship gate:** Cora teammate posts feature request in `#dev-<repo>`; plan generated and approved; PR appears with preview URL + reviewer findings; tap approve; merge fires; deploy fires. **One real PR shipped on a real repo.**

This playbook runs after Phase 2 merges to main. Requires `ANTHROPIC_API_KEY` in env or auth-profiles (`anthropic:default` as `sk-ant-api03-...`), `gh auth status` ok, a real GitHub repo to receive the PR, and the live orchestrator on `:8300`.

## Pre-conditions

```bash
# Services live
curl -sS http://127.0.0.1:8400/api/health   # router
curl -sS http://127.0.0.1:8300/api/health   # orchestrator (default_backend should be claude-p)

# CLIs
which claude && claude --version
which gh && gh auth status

# Authentication
test -n "$ANTHROPIC_API_KEY" && echo "env API key set" || \
  python3 -c "
import json
from pathlib import Path
d = json.load(open(Path.home() / '.openclaw/agents/main/agent/auth-profiles.json'))
t = d.get('profiles', {}).get('anthropic:default', {}).get('token', '')
print('auth-profiles API key prefix:', t[:10], '(must start with sk-ant-api for Phase 1b fallback)')
"

# Repo
ls -la $FLYN_DEFAULT_TEST_REPO/.git 2>&1   # default: ~/.flyn/orchestrator/test-repo
gh repo view "$(cd $FLYN_DEFAULT_TEST_REPO && git remote get-url origin)" --json name 2>&1 | head -5
```

For a clean test, ensure no stale `flyn/T-*` branches exist:

```bash
cd ~/.flyn/orchestrator/test-repo
git branch | grep flyn/ | xargs -n1 git branch -D 2>/dev/null
git worktree prune
git push origin --delete flyn/T-0001 flyn/T-0002 flyn/T-0003 2>/dev/null
```

## Procedure

### Step 1: Reset state.db for a clean run

```bash
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "DELETE FROM tasks; DELETE FROM task_events; UPDATE task_id_counter SET last=0;"
```

### Step 2: Send a synthetic dev task

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "ryan@telegram",
    "sender_role": "owner",
    "intent": "Add a file healthz.txt with the single line: ok, and commit it with message phase2-shipgate.",
    "external_message_id": "p2-shipgate-1",
    "raw_payload": {"channel": "telegram", "chat_id": 7191564227}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
echo "TASK_ID=$TASK_ID"
```

Expected: `{"task_id":"T-0001","state":"inbound","accepted":true}`.

### Step 3: Watch state transitions

```bash
for i in $(seq 1 20); do
  sleep 15
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    final_approval_pending) echo "PASS: reached final_approval_pending"; break ;;
    completed|deliverable_ready) echo "FALLBACK: hit terminal without PR (push or gh failed)"; break ;;
    failed|cost_paused|cancelled) echo "FAIL: $STATE"; break ;;
  esac
done
```

Expected: state advances through `triaging → routed → decomposed → dispatched → running → reviewed → final_approval_pending` in ~30-60s.

### Step 4: Confirm the real PR exists

```bash
TASK_INFO=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID)
echo "$TASK_INFO" | python3 -m json.tool
PR_URL=$(echo "$TASK_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("raw_payload",{}).get("pr_url","(none)"))')
echo "PR_URL=$PR_URL"

# Open the PR in your browser
open "$PR_URL"
```

Expected: `raw_payload.pr_url` is set, points at github.com, the PR body contains the title, rationale, reviewer findings, and verification fields.

### Step 5: Approve via REST

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"gate\": \"human_approval\",
    \"approver\": \"ryan\",
    \"approved\": true,
    \"reason\": \"ship-gate verification\"
  }" | python3 -m json.tool
```

Expected: `state` advances to `completed`.

### Step 6: Confirm PR merged

```bash
gh pr view $(echo "$PR_URL" | sed 's|.*/||') --json state,mergedAt --repo "$(cd ~/.flyn/orchestrator/test-repo && git remote get-url origin | sed 's/.git$//' | sed 's|.*github.com[/:]||')"
```

Expected: `state=MERGED, mergedAt!=null`.

### Step 7: Confirm Telegram notify landed (on your phone)

Manually verify that Ryan (chat_id 7191564227) received a Telegram message starting with `✅ T-0001 delivered` (Phase 1b outbound) and one starting with `👋 PR #...` if any prior PRs were stale.

### Step 8: Sign-off

- [ ] Pre-conditions all green
- [ ] Step 1-3: state machine ran from inbound → final_approval_pending
- [ ] Step 4: real PR appeared on GitHub with structured body
- [ ] Step 5-6: approval merged the PR
- [ ] Step 7: Telegram message received on Ryan's phone
- [ ] All 122 unit + integration tests pass: `cd ~/.flyn/orchestrator && .venv/bin/python -m pytest tests/`
- [ ] Ryan signs

Date: ____________  Ryan: ____________

## What this proves

If all 8 steps pass, Phase 2 is shipped per spec §8: a real claude-p worker built a real change in a real git worktree, opened a real PR via gh, the reviewer's structured findings landed in the PR body, and Ryan's approval merged the PR — end-to-end automation of the dev workflow.

## Deferred to Phase 2c

- Real PM-role LLM invocation (currently synthesizes a minimal plan dict from the intent; the PM prompt exists but isn't yet called)
- Multi-builder parallelism with file-domain locks (`LockManager` is built but not wired into `WorkerDispatcher`)
- TelegramChannelAdapter walk-me-through trigger detection (the `generate_walkthrough` function is built but no trigger detection in `ingest()` yet)
- ~~Router refactor (router.py is 607 lines; extract `_run_dev_pr_phase` and `handle_approval` into a separate module)~~ — **DONE 2026-05-16** (Phase 2c PR #8 merged; router.py at 578 lines; phase logic in `research_phase.py`/`content_phase.py`/`ops_phase.py`/`dev_phase.py`)
- Architect + Sanitizer roles (spec lists them; not yet implemented)
- Real preview-URL pipeline (Vercel/Cloudflare project tokens are in auth-profiles but the PR body doesn't pull them yet)

## Failure modes (Phase 1b + this phase)

- **OAuth contention:** if `auth-profiles.json` has an `sk-ant-oat` token (OAuth, not API key), the Phase 1b fallback skips it correctly. The worker uses OAuth from `~/.claude/.credentials.json` — which competes with interactive Claude Code sessions. Fix: generate a real `sk-ant-api03-*` key at console.anthropic.com and put it under `anthropic:default`.
- **No GitHub auth:** Phase 2 falls back to `deliverable_ready` (the Phase 1 MVP terminal) when `gh pr create` fails. Run `gh auth login` to fix.
- **Stale branches:** the Phase 1b WorktreeManager idempotency fix handles this autonomously now. If you see `decomposed → failed` immediately, check `git branch | grep flyn/` and `git worktree list --porcelain` in the test repo.
