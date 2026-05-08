# TOOLS — Chet (on Tune Outdoor's Mac)

What Chet has available locally. Loaded every turn. Reflects the install produced by `deploy/install-flyn.sh` from the `tune-outdoor` branch.

---

## Built-in OpenClaw capabilities

OpenClaw on Tune Outdoor's Mac (version installed during session 2). Commands used regularly:

- **Memory (native sqlite-vec + Gemini embeddings)** — `openclaw memory search`, `openclaw memory index`, `openclaw memory status`, `openclaw memory promote`
- **Cron** — `openclaw cron add/list/edit/rm/runs/status`. Native scheduling.
- **Channels** — `openclaw channels send/list` (Telegram, Slack, WhatsApp). **Google Chat is not in the built-in channel set; see "Pending integrations" below.**
- **Capability embedding** — `openclaw capability embedding create --provider gemini --text "..."`
- **Agent delegation** — `openclaw agent --agent <id> -m "..."` — spawn a sub-agent turn
- **Exec / shell** — primary `tool_use` surface. How Chet calls the Graphiti REST API and any other local service.

## Platform integrations

### OpenAI Codex (primary inference)

- **Auth model:** OAuth subscription (flat-rate). Tune Outdoor's subscription. Do NOT switch to pay-per-token.
- **Primary model:** `openai-codex/gpt-5.4`
- **Re-auth:** `openclaw models auth login --provider openai-codex`
- **Auth store:** `~/.openclaw/agents/main/agent/auth-profiles.json` → `openai-codex:*`

### Local inference (Ollama)

- **Substrate:** Ollama (version pinned by install script)
- **Heartbeat / background model:** `ollama/gemma4:e4b` — ~9.6 GB on disk, ~11 GB in Metal, auto-unloads after ~4 min idle
- **Auth profile:** `ollama:default` with `token: "local"`
- **Direct probe:** `curl http://localhost:11434/api/tags`

### Gemini (Google) — embeddings only

- **Model:** `gemini-embedding-001` (stable). Used via OpenClaw native embedding provider AND Graphiti's GeminiEmbedder.
- **Auth:** SAME API key under BOTH `gemini:default` AND `google:default` profiles. Embedding provider ID is `gemini`; runtime auth lookup uses `google`. Both entries needed.

### Messaging

- **Telegram (active):** Chet's dedicated bot. Used for primary operator interaction until Google Chat is wired in. Topics TBD with Kristian during BOOTSTRAP.
- **Google Chat (pending integration):** the Tune Outdoor team's intended primary channel. Not yet integrated; treat as a follow-up build (see "Pending integrations").

### Google Workspace

- **gog CLI** for Gmail, Calendar, Drive — Chet's Workspace user provisioned during session 2 §4.
- **Auth profile:** `google:default` (also doubles as the Gemini embeddings credential).

### File storage

- **Local:** `~/.openclaw/workspace/` (this dir) + Tune Outdoor's working dir (TBD with Kristian)
- **Cloud:** Google Drive via `gog` once Workspace user is provisioned

---

## Memory stack — what's deployed

Five layers, fast → slow / cheap → structured:

| # | Layer | Component | Access pattern |
|---|-------|-----------|----------------|
| 1 | Hot tier | `MEMORY.md` (<200 lines) | Always loaded in main-session |
| 2 | Conversation fidelity | **Lossless Claw plugin** in `plugins.slots.contextEngine` | DAG-based, raw messages preserved; `lcm_grep` / `lcm_describe` / `lcm_expand` recall compacted turns |
| 3 | Semantic recall over memory files | `openclaw memory search` → sqlite-vec + `gemini-embedding-001` | `openclaw memory search "query"` |
| 4 | Structured temporal KG | **Graphiti + Neo4j** behind Flask REST on `localhost:8100` | `curl` from the exec shell tool |
| 5 | Local embedding fallback | OpenClaw's built-in `embeddinggemma-300m-qat-Q8_0.gguf` via `memorySearch.fallback=local` | Kicks in when Gemini unreachable |

### How Chet calls the structured KG

Through `curl` from the exec tool. **Not** MCP. (See AGENTS.md "Structured memory" for full endpoint list and the architectural rationale.)

```bash
# Health check
curl -sS http://localhost:8100/api/health

# Ingest a fact — prose; Graphiti extracts entities + typed edges automatically
curl -sS -X POST http://localhost:8100/api/episode \
  -H 'Content-Type: application/json' \
  -d '{"body": "Tune Outdoor approved a $200 OpenAI subscription on 2026-05-08", "name": "openai-subscription-active"}'

# Semantic + graph search over facts
curl -sS 'http://localhost:8100/api/search?q=warranty'

# Temporal filter
curl -sS 'http://localhost:8100/api/temporal?q=warranty&from=2026-05-01&to=2026-05-31'
```

**Ingest timing:** POST /api/episode blocks 30–120 seconds while local `gemma4:e4b` runs entity-extraction. Normal. Wrapper script timeout > 300s.

**`group_id` is hardcoded to `flyn`** in this build (inherited from upstream). All Chet's episodes land under that group_id; fine because Chet is the only KG consumer on this Mac.

**If the API is down:** launchd agent `ai.flyn.graphiti-api`. Restart with `launchctl kickstart -k gui/$(id -u)/ai.flyn.graphiti-api`. Logs at `/tmp/flyn-graphiti-api.log`.

---

## Memory routing hierarchy (fastest/cheapest first)

1. `MEMORY.md` — pinned Hot-tier facts. Always in context.
2. Graphiti REST — typed + temporal + predicate queries. Use for "who/what/when."
3. `openclaw memory search` — semantic recall, fuzzy-worded queries.
4. Lossless Claw `lcm_*` tools — exact recovery of specific turns from compacted history.

Never use frontier cloud to "remember" something already in one of these four layers.

---

## Pending integrations (build follow-ups)

- **Google Chat plugin/bridge.** Tune Outdoor wants Chet primarily reachable from Google Chat. No off-the-shelf OpenClaw plugin exists. Approach: register a Google Chat app via Workspace admin, hit the Chat REST API from a thin OpenClaw plugin or webhook bridge. Out of scope for session 2's 3-hour block; Telegram-primary in the meantime.
- **Asana** (or whatever PM tool the team uses) — likely the system-of-record for tasks Chet coordinates. Confirm with Kristian.

---

## What is NOT installed (deliberate)

- **mem0** — Graphiti wins on temporal + schema evolution + no open CVE.
- **OpenClaw MCP integration for Graphiti** — proven not to work agent-turn-side in this OpenClaw version (see upstream postmortem). REST + curl is the pattern.
- **Obsidian** — deferred.

---

## How to pick the right tool

- **Quick recall of known state** → MEMORY.md first; Graphiti `/api/search`; last resort `openclaw memory search`.
- **Remember something durable** → POST to `/api/episode`. Markdown write to `workspace/memory/YYYY-MM-DD.md` as redundant backup if the heartbeat hasn't fired yet.
- **Temporal query** → Graphiti `/api/temporal`.
- **Fuzzy semantic search over session logs** → `openclaw memory search`.
- **Recovering a specific turn from compacted history** → Lossless Claw `lcm_grep` / `lcm_expand`.
- **External action (email, customer-facing post, prod write)** → approval gate in AGENTS.md.
- **Scheduled recurring work** → `openclaw cron add` or via `deploy/cron/register-flyn-crons.sh`. NOT inline.
- **Specialist focused task** → spawn sub-agent via `openclaw agent --agent <id>`; Chet coordinates.
- **Interactive / coordination / status / Q&A** → handle directly; Chet owns its turns.

---

## Anti-patterns

- **Don't use MCP-registered tools** — they hallucinate. Use REST + curl.
- **Don't use frontier cloud for background work** — heartbeat, embedding, fact-extraction stay local.
- **Don't replace `openclaw.json` wholesale** — always additive via `openclaw config set`.
- **Don't spawn sub-agents for work the main session can do quickly.**
- **Don't bypass approval gates** even when "probably wanted" — ask.
- **Don't spin up long-running background processes unless instructed or from cron.**
- **Don't surface Tune Outdoor team-member private DM context inside another member's thread or a public space.**
