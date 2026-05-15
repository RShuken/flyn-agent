# Phase 5 Ship Gate — Manual E2E

**Spec §8 Phase 5 ship gate:** One real low-risk ops task executed (rotate a test token); validator green; audit log populated. Strictest gates of all workflows — risk-tier classifier, mandatory dry-run for critical, before/after audit.

This playbook runs after Phase 5 merges to main. Requires the orchestrator on `:8300` running Phase 5 code, `ANTHROPIC_API_KEY` in env or auth-profiles, and `~/Work/ops-snapshots/` writable.

## Pre-conditions

```bash
curl -sS http://127.0.0.1:8400/api/health
curl -sS http://127.0.0.1:8300/api/health
mkdir -p ~/Work/ops-snapshots
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "DELETE FROM tasks; DELETE FROM task_events; DELETE FROM audit_log; UPDATE task_id_counter SET last=0;"
echo "stale-test-token-2025-01-01" > /tmp/flyn-shipgate-token.txt
```

The file at `/tmp/flyn-shipgate-token.txt` is the target — a fake "test token" that the ops workflow will rotate. Low-risk because the rule `(rotate|refresh).*test` matches → `low` tier → auto-execute, no approval.

## Procedure A — Low-tier auto-execute (rotate a test token)

### Step 1: Send a low-tier ops task

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "ryan@telegram",
    "sender_role": "owner",
    "intent": "rotate the test token at /tmp/flyn-shipgate-token.txt to a fresh value",
    "external_message_id": "p5-shipgate-low",
    "raw_payload": {"channel": "telegram", "chat_id": 7191564227}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
```

### Step 2: Watch transitions

```bash
for i in $(seq 1 20); do
  sleep 15
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    deliverable_ready) echo "PASS: validator green"; break ;;
    awaiting_owner_approval) echo "FAIL: low-tier should NOT block"; break ;;
    rejected) echo "FAIL: rejected"; break ;;
    failed|cancelled) echo "FAIL: $STATE"; break ;;
  esac
done
```

### Step 3: Inspect audit log

```bash
sqlite3 -header -column ~/.flyn/orchestrator/data/state.db \
  "SELECT actor, action, target, before_hash, after_hash, ts FROM audit_log WHERE task_id='$TASK_ID' ORDER BY id;"
```

Expected rows (in order):
- `actor=risk_classifier, action=pre-snapshot, target=/tmp/flyn-shipgate-token.txt, before_hash=<sha256>`
- `actor=executor, action=dry-run, target=/tmp/flyn-shipgate-token.txt`
- `actor=executor, action=post-snapshot, before_hash=<sha256-1>, after_hash=<sha256-2>` (hashes MUST differ)
- `actor=validator, action=validate, target=/tmp/flyn-shipgate-token.txt`

### Step 4: Confirm token actually rotated

```bash
cat /tmp/flyn-shipgate-token.txt
```

Expected: NOT `stale-test-token-2025-01-01` anymore. New value present.

## Procedure B — High-tier blocks for owner approval

### Step 5: Send a high-tier ops task

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "beth@telegram",
    "sender_role": "teammate",
    "intent": "deploy the orchestrator to production at 127.0.0.1:8301 with the new build",
    "external_message_id": "p5-shipgate-high",
    "raw_payload": {"channel": "telegram", "chat_id": 7434192034}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
```

### Step 6: Watch transitions

```bash
for i in $(seq 1 15); do
  sleep 15
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    awaiting_owner_approval) echo "PASS: high-tier halted at approval"; break ;;
    deliverable_ready) echo "FAIL: high-tier should NOT auto-execute"; break ;;
    failed|cancelled) echo "FAIL: $STATE"; break ;;
  esac
done
```

### Step 7: Inspect dry-run results

```bash
sqlite3 -header -column ~/.flyn/orchestrator/data/state.db \
  "SELECT actor, action, payload FROM audit_log WHERE task_id='$TASK_ID' ORDER BY id;"
```

Expected: only `pre-snapshot` and `dry-run` rows. NO `post-snapshot` or `validate` rows yet (execution gated).

### Step 8: Reject as owner

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"gate\": \"owner\",
    \"approver\": \"ryanshuken@gmail.com\",
    \"approved\": false,
    \"reason\": \"ship-gate verification — testing rejection path\"
  }" | python3 -m json.tool
```

Expected: state → `rejected`. Audit log gains a `reject` row with actor=ryanshuken@gmail.com.

## Procedure C — Critical-tier requires owner + rationale

### Step 9: Send a critical-tier ops task

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "beth@telegram",
    "sender_role": "teammate",
    "intent": "delete /tmp/flyn-shipgate-token.txt and wipe the test fixture directory",
    "external_message_id": "p5-shipgate-critical",
    "raw_payload": {"channel": "telegram", "chat_id": 7434192034}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
```

### Step 10: Wait for awaiting_owner_approval

```bash
for i in $(seq 1 15); do
  sleep 15
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  [ "$STATE" = "awaiting_owner_approval" ] && break
done
```

### Step 11: Verify teammate approval is REJECTED

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"gate\": \"critical\",
    \"approver\": \"beth@cora\",
    \"approved\": true,
    \"reason\": \"trying to approve as teammate\"
  }"
```

Expected: HTTP 403 or response with `error: not authorized — critical tier requires Owner`. Task stays at `awaiting_owner_approval`.

### Step 12: Verify owner approval WITHOUT rationale is REJECTED

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"gate\": \"critical\",
    \"approver\": \"ryanshuken@gmail.com\",
    \"approved\": true,
    \"reason\": \"\"
  }"
```

Expected: HTTP 400 or `error: critical tier requires written rationale`. Task stays at `awaiting_owner_approval`.

### Step 13: Approve as owner WITH rationale

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"gate\": \"critical\",
    \"approver\": \"ryanshuken@gmail.com\",
    \"approved\": true,
    \"reason\": \"ship-gate verification — file is a test fixture, recreatable, no production impact\"
  }" | python3 -m json.tool
```

Expected: state → `deliverable_ready`. Audit log has an `approved` row with actor=ryanshuken@gmail.com and the rationale stored in `payload`.

### Step 14: Inspect final audit trail

```bash
sqlite3 -header -column ~/.flyn/orchestrator/data/state.db \
  "SELECT actor, action, before_hash, after_hash, ts FROM audit_log WHERE task_id='$TASK_ID' ORDER BY id;"
```

Expected rows: pre-snapshot, dry-run, approved (with rationale in payload), post-snapshot (before_hash != after_hash), validate.

## Step 15: Sign-off

- [ ] Procedure A: low-tier task auto-executed → `deliverable_ready`
- [ ] Audit log has 4 rows for Procedure A (pre-snapshot, dry-run, post-snapshot, validate)
- [ ] Procedure B: high-tier halted at `awaiting_owner_approval` with only pre-snapshot + dry-run
- [ ] Rejection transitions to `rejected` with `reject` audit row
- [ ] Procedure C: critical-tier rejects teammate (auth error)
- [ ] Critical-tier rejects empty rationale (validation error)
- [ ] Owner + rationale succeeds; full 5-row audit trail
- [ ] One-way escalation honored: if rules say tier X, validator/router never executed at tier < X
- [ ] All 190 tests still pass (`pytest deploy/orchestrator/tests/`)
- [ ] Ryan signs

Date: ____________  Ryan: ____________

## What this proves

If all 15 steps pass, Phase 5 is shipped per spec §8: ops actions never bypass risk classification; low-risk auto-executes with full audit; medium/high/critical block for human approval; critical requires Owner + written rationale; before/after snapshots are SHA256-hashed and stored; one-way escalation prevents machines downgrading human-judged tier.

## Failure modes

- **PM emits ambiguous spec**: task → FAILED with reason "ops spec unparseable" (PM set tier to "(ambiguous)" or target empty).
- **Risk classifier LLM returns lower tier than rules**: `max_tier()` clamps to rule floor; `upgraded_from_rule=False` recorded; tier is the rule floor.
- **Dry-run discovers irreversible side effect**: task → AWAITING_OWNER_APPROVAL with dry_run_result flagged unsafe regardless of tier.
- **Post-snapshot hash matches pre-snapshot hash** (target didn't change): validator → FAIL with reason "executor reported success but target unchanged"; task → AWAITING_OWNER_APPROVAL.
- **Target unrecognized** (not file/http/cmd): pre-snapshot writes `before_hash=null` with payload describing the type; downstream validator still runs but cannot diff bytes.

## Deferred to Phase 5b (not blocking ship)

- Multi-target ops (current ops takes one target; multi-target = N parallel pipelines)
- Time-windowed approval (approvals expire after 1h for high+, force re-classification)
- Slack/email approval channels (only REST + Telegram MVP)
- Automatic rollback on validator FAIL (currently transitions to AWAITING_OWNER_APPROVAL; human decides revert)
- Validator-can-trigger-rollback policy (separate auth tier)
