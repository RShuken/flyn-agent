# Conversation Memory Slice 1 — Ship-Gate Playbook

**Verify all checks pass before declaring CM-01 done.**

## Prereqs

- memory-router on `:8400` running latest code: `bash deploy/memory-router/install.sh && launchctl kickstart -k gui/$(id -u)/ai.flyn.memory-router`
- `flyn-mem` CLI on PATH: `which flyn-mem` returns a binary
- `principals.json` exists with `ryan` mapped to your Telegram sender_id
- macOS Keychain unlocked
- Telegram bot `@flyn_4c_bot` online: `openclaw health` shows Telegram configured
- Ollama running with `gemma4:e4b`: `curl -s http://localhost:11434/api/tags | grep gemma4`

## Procedure A — Sources registry

### Step 1: conv adapter visible

```bash
flyn-mem sources | grep conv
```

Expected: a row showing the conv adapter with `default_included=True`.

## Procedure B — Live ingest roundtrip

### Step 2: send a real Telegram message

From your phone, send to `@flyn_4c_bot`:

> FLYN_SHIP_GATE_12345 testing slice 1

### Step 3: verify it landed in conv.db within 10s

```bash
sleep 10
flyn-mem conv search "FLYN_SHIP_GATE_12345"
```

Expected: one hit with that body text, sender_id matching your Telegram id.

### Step 4: verify summary fills in within 30s

```bash
sleep 20
flyn-mem conv health
flyn-mem conv search "FLYN_SHIP_GATE_12345"
```

Expected on first command: `summary_backlog` is 0 (or very low) for owner `ryan`.
Expected on second command: the hit's "summary:" line now shows a 1-2 sentence summary (not "pending").

### Step 5: cross-system query

```bash
flyn-mem query "FLYN_SHIP_GATE_12345" --top 5
```

Expected: at least one hit with `source: conv/telegram` and the right `metadata.msg_id`.

## Procedure C — Replay + audit

### Step 6: replay decrypts the original payload

Get the row_id from Step 3, then:

```bash
flyn-mem conv replay <row_id> --owner ryan
```

Expected: prints the original Telegram JSON payload (channel, chat_id, sender_id, text, etc.).

### Step 7: audit log captured the replay

```bash
sqlite3 ~/.flyn/memory-router/conv/owners.db \
  "SELECT ts, viewer, owned_by, op, q FROM audit_log ORDER BY id DESC LIMIT 5"
```

Expected: a row with `op = 'replay'`, `viewer = '$USER'`, `owned_by = 'ryan'`, `q = '<row_id>'`.

## Procedure D — Graphiti promotion

### Step 8: verify episode exists in Graphiti

```bash
curl -s "http://localhost:8100/api/episodes?group_id=flyn-ryan" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('episodes for flyn-ryan:', len(d.get('results', [])))"
```

Expected: at least 1 episode (more if you've sent multiple test messages).

## Sign-off

- [ ] Step 1: conv adapter in sources registry
- [ ] Step 2: real Telegram message sent
- [ ] Step 3: conv search finds the message within 10s
- [ ] Step 4: summary fills in within 30s
- [ ] Step 5: cross-system query includes the conv hit
- [ ] Step 6: replay decrypts the original payload
- [ ] Step 7: audit log captured the replay
- [ ] Step 8: Graphiti episode exists for the message

Date: ____________  Ryan: ____________

## What this proves

If all 8 steps pass, CM-01 is shipped per spec:
- Inbound Telegram → conv.db within seconds
- Async summarizer fills in summaries
- Per-owner Keychain encryption + audit-logged replay path
- Cross-source retrieval via the existing flyn-mem query surface
- Graphiti entity layer promoted for every message

## Deferred to slice 2 (not blocking)

- WhatsApp / iMessage / email connectors
- Outbound message mirroring (Flyn's replies)
- Cross-channel thread join
- Conversation → wiki auto-promotion
- Embedding-based semantic search
