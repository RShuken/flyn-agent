# Phase 4 Ship Gate — Manual E2E

**Spec §8 Phase 4 ship gate:** One draft delivered to requester's channel. Never auto-sent.

This playbook runs after Phase 4 merges to main. Requires the orchestrator on `:8300` running Phase 4 code, `ANTHROPIC_API_KEY` in env or auth-profiles, and `~/Work/content/` writable (or `FLYN_CONTENT_OUTPUT_ROOT` env override).

## Pre-conditions

```bash
curl -sS http://127.0.0.1:8400/api/health
curl -sS http://127.0.0.1:8300/api/health
mkdir -p ~/Work/content
sqlite3 ~/.flyn/orchestrator/data/state.db \
  "DELETE FROM tasks; DELETE FROM task_events; UPDATE task_id_counter SET last=0;"
```

## Procedure A — Draft-only delivery (default)

### Step 1: Send a content task with NO send intent

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "ryan@telegram",
    "sender_role": "owner",
    "intent": "draft a quick Telegram message to Beth asking her to share the OL sprint 1 retro notes",
    "external_message_id": "p4-shipgate-draft",
    "raw_payload": {"channel": "telegram", "chat_id": 7191564227}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
```

### Step 2: Watch transitions

```bash
for i in $(seq 1 20); do
  sleep 20
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    deliverable_ready) echo "PASS: draft delivered"; break ;;
    final_approval_pending) echo "UNEXPECTED — PM set wants_send=true; expected draft-only"; break ;;
    changes_requested) echo "EDITOR/FACT-CHECK BLOCKED — check critique findings"; break ;;
    failed|cancelled) echo "FAIL: $STATE"; break ;;
  esac
done
```

### Step 3: Confirm draft file exists

```bash
TASK_INFO=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID)
DRAFT_PATH=$(echo "$TASK_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("raw_payload",{}).get("draft_path","(none)"))')
echo "DRAFT_PATH=$DRAFT_PATH"
ls -la "$DRAFT_PATH"
cat "$DRAFT_PATH"
ls -la "$(dirname "$DRAFT_PATH")/.metadata.json" 2>/dev/null || ls -la "$(dirname "$DRAFT_PATH")"/*.metadata.json
```

Expected: a Markdown file at `~/Work/content/<slug>/<date>-<slug>.md` with a draft + a `.metadata.json` sidecar.

### Step 4: Confirm Telegram notify says "DRAFT"

Manually check Ryan's phone — message should start with `📝 DRAFT:` and contain the truncated draft text.

## Procedure B — Send flow (explicit send)

### Step 5: Send a content task WITH explicit send intent

```bash
RESP=$(curl -sS -X POST http://127.0.0.1:8300/api/tasks/inbound \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "sender_identifier": "ryan@telegram",
    "sender_role": "owner",
    "intent": "send Beth a Telegram message asking for the OL sprint 1 retro notes — be friendly, short",
    "external_message_id": "p4-shipgate-send",
    "raw_payload": {"channel": "telegram", "chat_id": 7191564227}
  }')
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')
```

### Step 6: Watch transitions

```bash
for i in $(seq 1 20); do
  sleep 20
  STATE=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')
  echo "$(date +%H:%M:%S) state=$STATE"
  case "$STATE" in
    final_approval_pending) echo "PASS: awaiting send approval"; break ;;
    deliverable_ready) echo "PM may have set wants_send=false; OK but means manual override needed"; break ;;
    failed|cancelled) echo "FAIL: $STATE"; break ;;
  esac
done
```

### Step 7: Inspect what would be sent

```bash
TASK_INFO=$(curl -sS http://127.0.0.1:8300/api/tasks/$TASK_ID)
echo "$TASK_INFO" | python3 -m json.tool
cat "$(echo "$TASK_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["raw_payload"]["draft_path"])')"
```

### Step 8: Approve via REST

```bash
curl -sS -X POST http://127.0.0.1:8300/api/tasks/$TASK_ID/approve \
  -H 'Content-Type: application/json' \
  -d "{
    \"task_id\": \"$TASK_ID\",
    \"gate\": \"send_externally\",
    \"approver\": \"ryan\",
    \"approved\": true,
    \"reason\": \"ship-gate verification\"
  }" | python3 -m json.tool
```

Expected: state → `completed`. The draft was sent to whatever destination the PM identified.

### Step 9: Confirm send actually happened

Manually check Beth's phone — she should have received the message.

If the PM's `send_destination` parsed to her chat_id (7434192034), the orchestrator called `TelegramChannelAdapter.send(channel="7434192034", body=draft)`. If not, the orchestrator logged a `content_send_deferred` memory event (check via Graphiti) and transitioned to COMPLETED without actually sending — which is the correct fallback per spec.

## Step 10: Sign-off

- [ ] Procedure A: draft-only flow ended at `deliverable_ready` with draft file written
- [ ] Telegram notify prepended with `📝 DRAFT:`
- [ ] Procedure B: send flow ended at `final_approval_pending` (NOT auto-sent)
- [ ] Approval transitioned to `completed`
- [ ] If destination was Telegram chat_id, the message actually landed on Beth's phone
- [ ] If destination was not Telegram, a `content_send_deferred` memory event was logged
- [ ] All 161 tests still pass
- [ ] Ryan signs

Date: ____________  Ryan: ____________

## What this proves

If all 10 steps pass, Phase 4 is shipped per spec §8: content drafts deliver as DRAFT by default; explicit send-via-X requires explicit approval; the Telegram bot adapter is the actual delivery channel; non-Telegram destinations are gracefully deferred rather than silently dropped.

## Deferred to Phase 4b

- Email send (IMAP/SMTP for `flynn@getcora.io` — Phase 6 prerequisite)
- Slack send (Slack MCP — needs Workspace OAuth)
- LinkedIn / Twitter publish (platform OAuth needed)
- Multi-step iteration on `CHANGES_REQUESTED` (writer gets a second pass with editor's findings)
- Tone-per-stakeholder voice files (`workspace/projects/<slug>/comms-tone.md` per spec §1)

## Failure modes

- **PM emits ambiguous spec**: task → FAILED with reason "PM spec unparseable or ambiguous" (PM set title to "(ambiguous)").
- **Writer produces no draft**: task → FAILED with reason "writer produced no draft".
- **Editor blocks**: task → CHANGES_REQUESTED. Read editor's edits via state.db audit log.
- **Fact-checker blocks** (only if needs_fact_check=true): task → CHANGES_REQUESTED with the findings logged.
- **Send fails for non-Telegram platform**: task → COMPLETED with `content_send_deferred` memory event. The draft is in the requester's channel; manual copy/paste sends it elsewhere.
