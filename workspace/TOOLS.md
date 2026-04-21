# TOOLS — Flyn (on 4C)

What Flyn has available on Mac Mini 4C. Loaded every turn. Reflects the ACTUAL working install as of 2026-04-21. See `POSTMORTEM-2026-04-21.md` for the full history of what we tried and what survived.

---

## Built-in OpenClaw capabilities

OpenClaw 2026.4.15 on 4C. Commands used regularly:

- **Memory (native sqlite-vec + Gemini 2 embeddings)** — `openclaw memory search`, `openclaw memory index`, `openclaw memory status`, `openclaw memory promote`
- **Cron** — `openclaw cron add/list/edit/rm/runs/status`. Native scheduling; use this not platform crontab.
- **Channels** — `openclaw channels send/list` (Telegram, Slack, WhatsApp, etc.)
- **Capability embedding** — `openclaw capability embedding create --provider gemini --text "..."` — ad-hoc embedding
- **Agent delegation** — `openclaw agent --agent <id> -m "..."` — spawn a sub-agent turn
- **Exec / shell** — the primary tool_use surface. This is how Flyn calls the Graphiti REST API and any other local service.

## Platform integrations

### Codex (OpenAI)

- **Auth model:** OAuth subscription (flat-rate). Do NOT switch to pay-per-token.
- **Primary model:** `openai-codex/gpt-5.4`
- **Re-auth:** `openclaw models auth login --provider openai-codex`
- **Auth store:** `~/.openclaw/agents/main/agent/auth-profiles.json` → `openai-codex:*`

### Local inference (Ollama)

- **Substrate:** Ollama 0.21 + (oMLX planned as future optimization, not required today)
- **Heartbeat / background model:** `ollama/gemma4:e4b` — 9.6 GB on disk, ~11 GB in Metal, auto-unloads after ~4 min idle
- **Auth profile:** `ollama:default` with `token: "local"` (required even though provider is local)
- **Direct probe:** `curl http://localhost:11434/api/tags`

### Gemini (Google) — embeddings only

- **Model:** `gemini-embedding-001` (stable). Used via OpenClaw native embedding provider AND Graphiti's GeminiEmbedder.
- **Auth:** store the SAME API key under BOTH `gemini:default` AND `google:default` profiles. OpenClaw's embedding provider ID is `gemini`; runtime auth lookup uses `google`. Both entries needed.

### Messaging (Telegram)

- **Primary channel:** Flyn's own dedicated Telegram bot for direct interaction with Ryan.
- **Topics:** `#flyn-briefing`, `#flyn-alerts`, `#flyn-ops`

### File storage

- **Local:** `~/.openclaw/workspace/` (this dir) + `~/Work/` (Ryan's active projects)
- **Cloud:** Google Drive via `gog` when needed

---

## Memory stack — what's actually deployed

Verified 2026-04-21 end-to-end. Five layers, fast → slow / cheap → structured:

| # | Layer | Component | Access pattern |
|---|-------|-----------|----------------|
| 1 | Hot tier | `MEMORY.md` (<200 lines) | Always loaded in main-session |
| 2 | Conversation fidelity | **Lossless Claw plugin** (Martian-Engineering 0.9.2) in `plugins.slots.contextEngine` | DAG-based, raw messages preserved; `lcm_grep` / `lcm_describe` / `lcm_expand` recall compacted turns |
| 3 | Semantic recall over memory files | `openclaw memory search` → sqlite-vec + `gemini-embedding-001` | `openclaw memory search "query"` |
| 4 | Structured temporal KG | **Graphiti + Neo4j** behind Flask REST on `localhost:8100` | `curl` from the exec shell tool (see next section) |
| 5 | Local embedding fallback | OpenClaw's built-in `embeddinggemma-300m-qat-Q8_0.gguf` via `memorySearch.fallback=local` | Kicks in when Gemini unreachable |

### How Flyn actually calls the structured KG (important — this is the REST pattern, NOT MCP)

The agent's exec tool emits real `tool_use` blocks for `curl`. These work; MCP-registered tools do not (see postmortem section "MCP-to-agent-turn"). Every call below goes through the exec tool.

```bash
# Health check
curl -sS http://localhost:8100/api/health

# Ingest a fact — prose; Graphiti extracts entities + typed edges automatically
curl -sS -X POST http://localhost:8100/api/episode \
  -H 'Content-Type: application/json' \
  -d '{"body": "Ryan approved the Railway cost cap increase on 2026-04-21", "name": "railway-cap-bump"}'

# Semantic + graph search over facts (returns edges with valid_at/invalid_at)
curl -sS 'http://localhost:8100/api/search?q=Railway+cost'

# Temporal filter — only facts valid within window
curl -sS 'http://localhost:8100/api/temporal?q=cora&from=2026-04-01&to=2026-04-30'

# Recent raw episodes
curl -sS 'http://localhost:8100/api/episodes?limit=10'
```

**Ingest timing:** POST /api/episode blocks 30–120 seconds while local gemma4:e4b runs the entity-extraction pipeline. Normal. Set any wrapping script's timeout > 300s.

**`group_id` is hardcoded to `flyn`.** Don't try to override per-call.

**If the API is down:** the service is a launchd agent `ai.flyn.graphiti-api`. Restart with `launchctl kickstart -k gui/$(id -u)/ai.flyn.graphiti-api`. Check logs at `/tmp/flyn-graphiti-api.log`.

---

## Memory routing hierarchy (fastest/cheapest first)

1. `MEMORY.md` — pinned Hot-tier facts. Always in context.
2. Graphiti REST — typed + temporal + predicate queries. Use for "who/what/when."
3. `openclaw memory search` — semantic recall, fuzzy-worded queries.
4. Lossless Claw `lcm_*` tools — exact recovery of specific turns from compacted history.

Never use frontier cloud to "remember" something already in one of these four layers.

---

## What is NOT installed (deliberate)

- **mem0** — schemaless, ADD-only after v2.0, open CVE, weak temporal reasoning. Graphiti wins on every axis Flyn cares about.
- **Obsidian** — deferred. Add later if visual graph inspection becomes a felt need.
- **OpenClaw MCP integration for Graphiti** — proven not to work agent-turn-side in 2026.4.15 (see postmortem). REST + curl is the pattern.
- **oMLX** — future optimization. Ollama 0.21 with Metal is working for now.

---

## How to pick the right tool

- **Quick recall of known state** → start with Hot tier (MEMORY.md reads are free); if absent, Graphiti `search_facts`; last resort `openclaw memory search`.
- **Remember something durable** → POST to `/api/episode`. Markdown write to `workspace/memory/YYYY-MM-DD.md` as redundant backup if the heartbeat hasn't fired yet.
- **Temporal query** ("what happened between X and Y") → Graphiti `/api/temporal`.
- **Fuzzy semantic search over session logs** → `openclaw memory search`.
- **Recovering a specific turn from compacted history** → Lossless Claw `lcm_grep` / `lcm_expand`.
- **External action (email, post, prod write)** → approval gate in AGENTS.md.
- **Scheduled recurring work** → `openclaw cron add`, NOT inline.
- **Specialist focused task** → spawn sub-agent via `openclaw agent --agent <id>`; Flyn coordinates.
- **Interactive / creative / ideation with Ryan** → handle directly; Flyn owns its turns.

---

## Anti-patterns

- **Don't use MCP-registered tools** — they hallucinate. Use the REST + curl pattern.
- **Don't use frontier cloud for background work** — heartbeat, embedding, fact-extraction all stay local.
- **Don't replace `openclaw.json` wholesale** — always additive via `openclaw config set`. The file carries other skills' auth.
- **Don't spawn sub-agents for work the main session can do quickly.**
- **Don't bypass approval gates** even when "probably wanted" — ask.
- **Don't spin up long-running background processes unless instructed or from cron.**
- **Don't send Ryan info he's already seeing in-session.**
