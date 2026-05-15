# Phase 0 Ship Gate — Manual E2E

**Spec §8 gate:** Real Telegram message → MemoryRouter ingest → Graphiti episode appears + markdown summary file written + dedup blocks the same message replayed.

## Pre-conditions
- `flyn-memory-router` running on 8400 (curl health)
- `ai.flyn.graphiti-api` running on 8100 (curl health)
- `@flyn_4c_bot` Telegram bot live
- Ryan (chat_id 7191564227) is the sender

## Procedure

1. **Send a Telegram DM** to `@flyn_4c_bot`: "Smoke test message for Phase 0 ship gate, timestamp $(date -Iseconds)"

2. **Manually invoke the ingest path** (until the channel adapter ships in Phase 1):
   ```bash
   TS=$(date -Iseconds)
   DEDUP=tg-msg-$(date +%s)
   curl -sS -X POST http://127.0.0.1:8400/api/memory/ingest \
     -H 'Content-Type: application/json' \
     -d "{
       \"source\": \"telegram\",
       \"event_type\": \"inbound_message\",
       \"subject\": \"ryan-dm-smoke\",
       \"body\": \"Ryan said: Smoke test message for Phase 0 ship gate at $TS\",
       \"dedup_key\": \"$DEDUP\",
       \"sender_role\": \"owner\"
     }" | python3 -m json.tool
   ```
   Expected: `{"accepted":true, "deduped":false, "importance":"warm", "tiers_written":["warm"], ...}`

3. **Confirm Graphiti got the episode:**
   ```bash
   curl -sS 'http://127.0.0.1:8100/api/search?q=Smoke+test'
   ```
   Expected: JSON with at least one fact whose body contains "Smoke test."

4. **Confirm workspace markdown was written:**
   ```bash
   ls -lt ~/.openclaw/workspace/memory/ | head -3
   cat ~/.openclaw/workspace/memory/*ryan-dm-smoke*.md 2>/dev/null
   ```
   Expected: a fresh `<date>-ryan-dm-smoke.md` file with the prose body.

5. **Dedup test — replay the exact same call from step 2 (same $DEDUP):**
   Re-run the curl above WITHOUT changing $DEDUP.
   Expected: `{"accepted":true, "deduped":true, "tiers_written":[], "notes":["skipped: dedup hit"]}`
   Confirm no new Graphiti episode + no new workspace file.

6. **Permanent pin test:**
   ```bash
   curl -sS -X POST http://127.0.0.1:8400/api/memory/pin \
     -H 'Content-Type: application/json' \
     -d "{\"subject\":\"Phase 0 ship gate\",\"body\":\"passed $(date -Iseconds)\",\"sender_role\":\"owner\"}"
   grep "Phase 0 ship gate" ~/.openclaw/workspace/MEMORY.md
   ```
   Expected: MEMORY.md contains the pinned line in `## Active pins`.

7. **Decay no-op test** (since pin is permanent):
   ```bash
   curl -sS -X POST http://127.0.0.1:8400/api/memory/maintenance/decay \
     -H 'Content-Type: application/json' \
     -d '{"sender_role":"owner"}'
   grep "Phase 0 ship gate" ~/.openclaw/workspace/MEMORY.md
   ```
   Expected: pin still present.

8. **Sign-off checklist:**
   - [ ] Steps 1–7 all returned expected outcomes
   - [ ] All L1 + L2 unit + integration tests green (`pytest -v` in `deploy/memory-router/`)
   - [ ] `flyn-sanitize deploy/memory-router/flyn_memory_router` is clean
   - [ ] Workspace file changes committed and rsync'd to live
   - [ ] Ryan signs this checklist

Date: ____________  Ryan: ____________
