# Install memory stack — Flyn on 4C

Ordered install runbook for Flyn's in-depth memory stack. Run from the top — each step depends on the previous.

**Target host:** Mac Mini 4C (Apple Silicon)
**OpenClaw version:** 2026.4.15+
**Expected end state:** Lossless Claw as context engine, Gemini 2 embeddings (cloud) with EmbeddingGemma local fallback, sqlite-vec vector store, mem0 structured memory, Gemma 4 local heartbeat, Hot/Warm/Cold tiering, MEMORY.md group-chat gate.

See the upstream library at `../skills/memory-options/*.md` for per-option detail.

---

## Privacy note

**`gemini-embedding-2-preview` is a cloud API call.** The text being embedded leaves the machine and hits Google's servers. This stack chooses it for retrieval quality (#1 MTEB, multimodal). The local fallback `embeddinggemma` kicks in when quota exhausts OR Flyn is offline — so retrieval still works without the cloud round-trip, just at slightly lower quality.

If 100% on-device is a hard requirement, flip `primary` and the local fallback in `openclaw.json` → `memory.embedding`. Everything else in this runbook stays the same.

---

## Step 0 — Pre-flight checks

```bash
# On 4C (SSH via ssh 4c-raw per feedback_ssh_commands):
openclaw health
openclaw doctor
openclaw --version   # expect 2026.4.15+
ollama --version 2>&1 || brew install ollama  # Ollama required; install if missing
node --version       # expect 22 LTS via tarball path (NOT Homebrew Node 25 — see postmortem_ian_ferguson_2026-04-17)
```

If any of these fail, resolve before proceeding.

---

## Step 1 — Lossless Claw (context engine)

Single biggest leverage in the stack. Sidesteps compaction hallucinations. Install FIRST — once compaction has happened without Lossless Claw, those facts are already lossy.

Reference: `../skills/memory-options/lossless-claw.md`

```bash
# Install via OpenClaw skills registry
openclaw skills install lossless-claw

# Verify the contextEngine slot is populated
openclaw config get contextEngine
# Expect: { "provider": "lossless-claw" }
```

If `skills install` isn't available, fall back to:
```bash
openclaw plugins install lossless-claw   # or
npm install -g lossless-claw && openclaw config set contextEngine.provider lossless-claw
```

Then verify in a test session:
```bash
openclaw session start
# Run a long enough session to trigger compaction, then verify facts survive:
openclaw memory search "specific detail from earlier in session"
```

---

## Step 2 — Local inference substrate (Ollama / oMLX)

Flyn uses local models for ALL background work (heartbeats, crons, embeddings fallback). Apple Silicon native preferred.

### 2a — Ollama baseline

```bash
# Install Ollama if not present
brew install ollama
brew services start ollama
curl http://localhost:11434/api/tags   # expect empty list of models
```

### 2b — Pull Gemma 4 for heartbeats

```bash
# Gemma 4 — multimodal, 128K–256K context, native tool-use, NVIDIA RTX first-class
# Size: 4B-active MoE is the efficient default; 31B dense if RAM permits
tmux new-session -d -s gemma4-pull "ollama pull gemma4:e4b"
# (Wrap in tmux per feedback_oac_tmux_long_running: ollama pull may exceed OAC 10-min PTY timeout)
ollama list | grep gemma4
```

### 2c — (Optional, recommended) Upgrade to oMLX substrate

Apple Silicon native. 2× faster, ~50% less RAM than Ollama for the same model. Reference: `../skills/memory-options/omlx-apple-silicon.md`.

```bash
# Install oMLX (check latest path on omlx.run or Hugging Face MLX Community)
brew install omlx   # placeholder — confirm install method against upstream
omlx pull gemma4:e4b
omlx serve &        # or launchd/brew services
curl http://localhost:8081/v1/models
```

Keep Ollama installed too — some skills may still reference it.

---

## Step 3 — Embeddings (Gemini 2 primary + EmbeddingGemma local fallback)

### 3a — Gemini API key

```bash
# Get a key: https://aistudio.google.com/app/apikey (free tier is generous)
# Store in auth-profiles.json, NOT in environment or git:
openclaw models auth login --provider google-gemini
# Follow prompts; paste API key; key lands at:
# ~/.openclaw/agents/main/agent/auth-profiles.json
```

Verify:
```bash
openclaw models auth list | grep google-gemini
# Expect: google-gemini  OK
```

### 3b — Local fallback model

```bash
ollama pull embeddinggemma   # 308M params; Matryoshka embeddings
ollama list | grep embeddinggemma
```

### 3c — Wire into openclaw.json

`openclaw.json` already has the `memory.embedding` block (see root of this repo). Confirm OpenClaw picked it up:

```bash
openclaw memory status --json | jq '.embedding'
# Expect: {
#   "primary": { "provider": "google-gemini", "model": "gemini-embedding-2-preview" },
#   "fallbacks": [ ... embeddinggemma is the last entry ... ]
# }
```

### 3d — Test embedding + fallback

```bash
# Primary test (should hit Gemini cloud)
openclaw memory test-embed "hello world"

# Simulate quota exhaustion: temporarily rotate the key to invalid, confirm fallback kicks in
openclaw memory test-embed --force-fallback "hello world"
# Expect: local embeddinggemma produces vector; no error propagates
```

---

## Step 4 — Vector store (sqlite-vec — already built-in)

OpenClaw's built-in `sqlite-vec` is fine at Flyn scale. No install needed. Confirm:

```bash
openclaw memory status --json | jq '.vectorStore'
# Expect: { "provider": "sqlite-vec" }
```

If Flyn ever grows beyond single-digit GB of embeddings or needs per-attribute filtering, see `../skills/memory-options/vector-dbs-alternatives.md` (LanceDB for scale, Qdrant for rich filters). Not needed now.

---

## Step 5 — Hot / Warm / Cold tiering

This is a **file-organization pattern**, not a separate install. See `../skills/memory-options/community-patterns.md`.

```bash
# Create the tier directories
mkdir -p ~/.openclaw/workspace/memory/warm
mkdir -p ~/.openclaw/workspace/memory/cold
mkdir -p ~/.openclaw/workspace/memory/structured  # for mem0 (next step)
```

The weekly-memory-rollup heartbeat (see HEARTBEAT.md) moves files between tiers automatically. MEMORY.md stays Hot (<200 lines), group-chat-gated by AGENTS.md boot routing.

---

## Step 6 — Structured memory (mem0)

Entity + relationship + preference storage. Official OpenClaw integration. Apache 2.0. Reference: `../skills/memory-options/mem0.md`.

```bash
# Install the mem0 skill
openclaw skills install mem0

# Verify
openclaw skills list | grep mem0
# Expect: mem0   ready
```

mem0 backs onto a local SQLite file by default (matches our local-first stance):
```bash
openclaw memory status --json | jq '.structured'
# Expect: { "provider": "mem0", "backend": "sqlite", "path": "~/.openclaw/workspace/memory/structured/mem0.db" }
```

Test:
```bash
openclaw memory remember --structured "Ryan prefers Codex OAuth over per-token billing for cost reasons"
openclaw memory search --structured "Ryan cost preference"
# Expect: the stored fact comes back with metadata
```

---

## Step 7 — Heartbeat auto-save

Every heartbeat cycle, Flyn extracts structured facts from session activity and writes them to `workspace/memory/YYYY-MM-DD.md` + updates mem0. Local Gemma 4 does the extraction — no cloud call.

This pulse is defined in HEARTBEAT.md → `hourly-memory-auto-save`. Register it in step 8.

---

## Step 8 — Register cron jobs

```bash
openclaw cron add --name hourly-memory-save \
  --cron "0 6-23 * * *" \
  --command "~/.openclaw/scripts/memory-autosave.sh" \
  --env "HEARTBEAT_TRIAGE_MODEL=ollama/gemma4:e4b"

openclaw cron add --name weekly-memory-rollup \
  --cron "0 20 * * 0" \
  --command "~/.openclaw/scripts/memory-rollup.sh"

openclaw cron list
# Verify both registered, next-run times look correct
```

Scripts `memory-autosave.sh` and `memory-rollup.sh` are created during BOOTSTRAP — skeletons live in `../skills/memory-options/community-patterns.md` (reference their fact-extraction + rollup patterns).

---

## Step 9 — Security gate enforcement

Already wired in `workspace/AGENTS.md` session-type routing table. Verify:

```bash
# Test in an intentional group-chat session context:
openclaw session start --mode group
# Inside, attempt:
openclaw memory search "anything"
# Expect: refusal with message "MEMORY.md not loaded in group-chat context per AGENTS.md"
```

---

## Step 10 — Post-install validation

```bash
openclaw health
openclaw doctor
openclaw memory status --json

# End-to-end smoke test
openclaw agent --agent main -m "remember that our staging DB is at db-staging.local and the prod one is at db-prod.local; Ryan is the only person who approves schema migrations"
# (session ends, compaction happens later or on-demand)
openclaw memory search "schema migrations approver"
# Expect: fact returned with "Ryan" as approver
```

If the final search returns the stored fact through compaction, the stack is working end-to-end.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `gpt-5.4` resolves as "Unknown model" | GitHub `openclaw/openclaw#37623`/`#55461` | See `openclaw.json` `agents.defaults.models.openai-codex/gpt-5.4.contextTokens` override already set; verify with `openclaw models list --all` |
| Gemini embedding returns 403 | API key wrong, or quota exhausted | `openclaw models auth login --provider google-gemini` to re-auth; if quota, fallback kicks in automatically — check `openclaw logs tail` |
| Lossless Claw not in `contextEngine` slot | Install didn't complete | Re-run `openclaw skills install lossless-claw`; check `openclaw config get contextEngine` |
| Gemma 4 pull timed out | Large model, long pull on flaky net | `tmux` wrap: `tmux new-session -d -s pull "ollama pull gemma4:e4b"` then reattach |
| Heartbeat hits cloud instead of local | Env var not set | Confirm `HEARTBEAT_TRIAGE_MODEL=ollama/gemma4:e4b` in the cron registration; also check `agents.defaults.heartbeat.model` in openclaw.json |
| mem0 writes to wrong path | Default path conflict with an existing install | Override via `openclaw config set memory.structured.path ~/.openclaw/workspace/memory/structured/mem0.db` |
| Compaction still losing facts with Lossless Claw installed | Lossless Claw needs summary model | Set `openclaw config set contextEngine.summaryModel ollama/gemma4:e4b` |
