# TOOLS — Flyn (on 4C)

What Flyn has available on Mac Mini 4C. Loaded every turn.

---

## Built-in OpenClaw capabilities

Verified on 4C via `openclaw skills list` (2026-04-19 probe). 2026.4.15+ install.

- **Memory** — `openclaw memory search`, `openclaw memory index`, `openclaw memory status`
- **Cron** — `openclaw cron add/list/edit/rm/runs/status`. Native scheduling; do NOT use platform crontab as first choice.
- **Channels** — `openclaw channels send/list` (send Telegram/Slack/Discord messages)
- **Agent delegation** — `openclaw agent --agent <id> -m "..."` (for focused sub-agent runs)
- **Models** — `openclaw models status/list/auth`. Primary: `openai-codex/gpt-5.4` via subscription OAuth.
- **Bundled skills on 4C (43/52 ready per probe):** `1password`, `apple-notes`, `apple-reminders`, `bear-notes`, `blogwatcher`, `blucli`, `camsnap`, `clawhub`, `coding-agent`, etc.

## Platform integrations

### Codex / OpenAI

- **Auth model:** subscription OAuth (flat-rate). Do NOT switch to pay-per-token without approval.
- **Auth location:** `~/.openclaw/agents/main/agent/auth-profiles.json`
- **Re-auth:** `openclaw models auth login --provider openai-codex`
- **Known issues:** `gpt-5.4` may resolve as "Unknown model" on older OpenClaw versions — see `skills/deploy-model-routing.md` "Platform caveats" for the `models.providers` override.

### Local inference (Ollama / oMLX)

- **Substrate:** oMLX preferred on Apple Silicon (2× faster, 50% less RAM than Ollama). See `skills/memory-options/omlx-apple-silicon.md`.
- **Default heartbeat model:** Gemma 4 (or Qwen 3.5 8B as alternative). See `skills/memory-options/gemma4-heartbeat.md`.
- **Embeddings:** `mxbai-embed-large` via oMLX for local, `gemini-embedding-001` for cloud.
- **All background traffic routes here** — never send heartbeat/cron/embedding calls to Codex.

### Messaging (Telegram)

- **Primary channel:** Flyn's own Telegram bot — DO NOT reuse Rel's bot token.
- **Topics:** `#flyn-briefing` (morning digest), `#flyn-alerts` (failures), `#flyn-ops` (ad-hoc status)
- **Rel HQ group:** send here for cross-agent notifications; respect that it's also Rel's space.
- See `skills/channels/telegram.md` for channel config.

### OAC gateway (pairing with Rel)

- **Purpose:** Rel → Flyn dispatch + Flyn → Rel reports.
- **Auth:** OAC enrollment codes per `RShuken/openagent-connect`.
- **Rule:** Rel decides, Flyn executes. Do NOT initiate user-facing interactive turns over OAC — route those to Rel and let Rel handle the user.

### File storage

- **Local:** `~/.openclaw/workspace/` (this directory) + `~/Work/` (Ryan's active projects)
- **Cloud:** Google Drive via `@steipete/gog` (OAuth) when needed for document exchange

## Memory stack (installed per `deploy/install-memory-stack.md`)

Flyn's in-depth memory is multi-layer. Each layer has a specific failure mode it addresses.

| Layer | Component | Role | Local / Cloud |
|-------|-----------|------|----------------|
| Context engine | **Lossless Claw** | Sidesteps compaction hallucinations (`openclaw/openclaw#58137/#65218/#66947/#44787`) | local |
| Embedding primary | **`gemini-embedding-2-preview`** | #1 MTEB multimodal, retrieval quality | cloud (Google API) |
| Embedding stable fallback | `gemini-embedding-001` | If preview has availability issues | cloud |
| Embedding local fallback | **EmbeddingGemma** | Kicks in when Gemini quota exhausts or offline | local |
| Vector store | **`sqlite-vec`** (built-in) | Vector storage | local |
| Structured memory | **mem0** | Entity + relationship + preference store | local (SQLite backend) |
| Background chat model | **Gemma 4** via Ollama/oMLX | All heartbeat / cron / fact-extraction calls | local |
| Tiering | **Hot / Warm / Cold** file pattern | MEMORY.md < 200 lines stays Hot; daily → warm; weekly → cold | local |
| Security gate | **AGENTS.md session-type routing** | MEMORY.md never loads in group chat / sub-agent | policy |

How Flyn queries memory:

```bash
# Unstructured recall (vector search)
openclaw memory search "what Ryan decided about Claude vs Codex"

# Structured recall (mem0)
openclaw memory search --structured "deployment approver for schema migrations"

# Store a fact
openclaw memory remember "some fact to pin"
openclaw memory remember --structured "typed entity+relationship fact"

# Status / diagnostics
openclaw memory status --json
```

## Skills installed in this workspace

Canonical list kept in the upstream `openclaw-base` repo under `skills/`. Flyn's active set (verify on 4C with `openclaw skills list`):

| Skill | Purpose | Trigger |
|-------|---------|---------|
| `deploy-memory` | Baseline memory subsystem | always on |
| `deploy-memory-advanced` + `memory-options/*` | Extended memory catalog (see memory stack above) | always on |
| `lossless-claw` | Context engine slot | always on |
| `mem0` | Structured entity memory | always on |
| `deploy-model-routing` | Tier routing + cost strategy | always on |
| `deploy-security-safety` | Prompt-injection hygiene | always on |
| (add more as installed) | | |

## Per-skill model overrides

When a specific skill benefits from a cheaper/local model (per `skills/_authoring/_deploy-common.md` Per-skill env-var overrides):

| Skill | Env var | Value |
|-------|---------|-------|
| `deploy-urgent-email` (when enabled) | `URGENT_EMAIL_MODEL` | `openai-codex/gpt-5.4-nano` |
| heartbeat rollups | `HEARTBEAT_TRIAGE_MODEL` | `ollama/gemma4:e4b` |

Add rows as skills install and get tuned.

## How to pick the right tool

- **Quick recall of known state** → `openclaw memory search` first (cheap, fast)
- **Web-fresh information** → search skill (Tavily/brave) — flag to Ryan that a web query is happening
- **External action (email, post, prod write)** → approval gate in AGENTS.md
- **Scheduled recurring work** → `openclaw cron add`, NOT inline
- **Background fact-extraction** → heartbeat auto-save pattern (`skills/memory-options/community-patterns.md`)
- **Interactive / creative / ideation** → route to Rel over OAC

## Anti-patterns

- Don't use channels to send Ryan info he's already seeing in-session.
- Don't spawn sub-agents for work the main session can do quickly.
- Don't call LLMs in a loop for things a structured memory query can answer.
- Don't bypass approval gates even when "probably wanted" — ask.
- Don't spin up background processes unless instructed or from cron.
