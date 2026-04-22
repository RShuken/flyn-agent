# Flyn Deployment Postmortem — 2026-04-21

Long-form record of what broke, what worked, and why Flyn's install runbook looks the way it does. Written immediately after the install so the mistakes stay concrete.

---

## Architecture as shipped

```
Ryan (operator, via Telegram + direct)
   │
   ▼
Flyn — CEO of Mac Mini 4C (OpenClaw 2026.4.15)
   ├── Primary model:  openai-codex/gpt-5.4 via OAuth subscription  (cost: flat-rate)
   ├── Background:     ollama/gemma4:e4b (11 GB Metal, ~4 min idle unload)
   ├── Context engine: Lossless Claw plugin (Martian-Engineering 0.9.2) in contextEngine slot
   ├── Embeddings:     OpenClaw memory:   Gemini (gemini-embedding-2-preview) — cloud
   │                   Graphiti REST:     Gemini (gemini-embedding-001)        — cloud
   │                   (two-embedder split — see install-flyn.sh step 7 note)
   ├── Local fallback: EmbeddingGemma 300M via OpenClaw native GGUF path
   ├── Vector store:   built-in sqlite-vec
   └── Structured KG:  Graphiti + Neo4j behind Flask REST on localhost:8100
                       called by the agent via curl from the exec shell tool
```

No MCP-to-agent-turn integration. See "Things that don't work" below.

---

## Things that DO work

1. **OpenClaw 2026.4.15** on Apple Silicon + tarball Node 22 LTS. (Do NOT use Homebrew Node 25 — see Ian Ferguson postmortem in memory.)
2. **Gemma 4 `gemma4:e4b`** via Ollama 0.21.0 with `ollama:default` auth profile. Tool calling works natively.
3. **Gemini embeddings** after storing the API key under BOTH `gemini:default` AND `google:default` profiles.
4. **Lossless Claw plugin** via `openclaw plugins install @martian-engineering/lossless-claw` (bare npm spec, no prefix). Auto-wires into `plugins.slots.contextEngine`.
5. **Graphiti REST wrapper on localhost:8100**, reached by the agent via `curl` emitted by the exec tool. This is the production pattern Edge (Dan Caruso's agent) uses.
6. **Neo4j 5.26 in Docker** with `NEO4J_server_memory_heap_max__size=1G` cap. Uses ~830 MiB steady-state on 16 GB host.
7. **launchd plist** `ai.flyn.graphiti-api.plist` for the REST service — auto-start at boot, auto-restart on crash, 30s throttle.
8. **openai-codex/gpt-5.4 as interactive primary** via OAuth subscription. Emits tool_use blocks correctly for exec; does NOT emit for MCP tools (see below).
9. **Heartbeat routes to local Gemma 4**. Proven by Ollama GPU load time-aligned with heartbeat fires, and by zero `model_fallback_decision` log entries post-config.

---

## Things that DON'T work (so we stopped trying)

1. **MCP-to-agent-turn on OpenClaw 2026.4.15** — six registration paths tried:
   - `openclaw mcp set <name> <json>` (global `mcp.servers.*`)
   - `plugins.entries.acpx.config.mcpServers.*` + ACPX enabled
   - `agents.defaults.embeddedHarness.runtime = codex` harness swap
   - `--local` embedded mode
   - Primary model swap Codex → Claude Sonnet 4.6 (to rule out OpenAI codex tool_use regression)
   - `@aiwerk/openclaw-mcp-bridge` community plugin, installed + activated + reports "1 servers configured"
   
   Every attempt resulted in the agent hallucinating the tool call (producing confident "Added" text) while Neo4j's episode count stayed unchanged and zero MCP traces appeared in the gateway log. Conclusion: there is a plumbing gap between OpenClaw's agent runtime and MCP that config alone can't close in this version. Edge (production reference) confirmed they also don't use MCP-to-agent-turn — they use REST + curl.

2. **Gemma 3:4b as heartbeat model** — rejected OpenClaw's tool schema with HTTP 400 "provider rejected the request schema or tool payload." Gemma 3 lacks native tool-calling at this size. Gemma 4 fixes it.

3. **`openclaw capability model run --model X`** as a routing probe — the `--model` flag doesn't reliably bind. Response always shows `provider: openai-codex` regardless of the override. Use Ollama's own log + gateway `model_fallback_decision` log entries as the real source of truth.

4. **Invented openclaw.json keys** — keys rejected by the live schema validator included: `memory.embedding.*`, `memory.contextEngine`, `memory.vectorStore`, `memory.structured`, `agents.defaults.models.X.contextTokens`, `agents.defaults.models.X.kind`, root-level `providers`, `_comment`. The real schema is narrower; use `openclaw config schema | python3 -c ...` to dump it before writing config.

5. **Replacing openclaw.json wholesale** — the existing file on a live install carries `skills.entries.*.apiKey` legacy secrets, `plugins.entries.*`, `hooks.internal.entries.*`, `tools.web.*`, `meta`, `wizard`, `session`, `auth.token` — all load-bearing. Any install script MUST use `openclaw config set` (which merges) and not overwrite the file.

6. **`brew services restart openclaw`** — OpenClaw is npm-installed, not a Homebrew formula. Gateway restart is `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`.

---

## Hard-earned gotchas (the "do NOT repeat" list)

| # | What I did | What broke | The fix |
|---|-----------|------------|---------|
| 1 | Wrote openclaw.json with invented keys | Validator rejected every key I made up; `openclaw health` failed | Dump `openclaw config schema` first; only use `openclaw config set` for additive changes |
| 2 | Overwrote live openclaw.json | Clobbered Notion, OpenAI Whisper, Goplaces API keys + plugin/hook/tool config | Backup + merge, never replace |
| 3 | Pulled `gemma3:4b` thinking `:4b` was a version | Gemma 3 4B doesn't tool-call, every heartbeat fell back to Codex | Tag is `gemma4:e4b` — `e4b` = edge-4B variant of Gemma 4 |
| 4 | Assumed `ollama/gemma3:4b` heartbeat would "just work" | Got 401 "No API key for provider ollama" | Even local providers need an `ollama:default` auth profile with `token: "local"` |
| 5 | Stored Gemini key as `gemini:default` | Embedding call failed: "No API key for provider google" | Also store under `google:default` — two profile IDs, same token |
| 6 | Swapped primary to Claude "to fix MCP tool use" | Didn't fix MCP and burned Anthropic tokens | Primary stays Codex. Claude swap was speculative and wrong. MCP issue is upstream plumbing, not model. |
| 7 | Built the whole memory stack around MCP registration | 6 hours of rabbit-holing; agent always hallucinated | Check production references (Edge) BEFORE committing to an abstraction |
| 8 | Registered Gemini key in `plugins.entries.openclaw-mcp-bridge.config.servers.env` block | Secrets in openclaw.json (plaintext) | Read from auth-profiles.json in a launcher wrapper script; keep secrets out of openclaw.json |
| 9 | `brew services restart openclaw` to reload config | Error: "no openclaw formula"; gateway died | `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway` |
| 10 | 120s timeout on the Graphiti REST POST | TimeoutError during entity extraction (multiple local LLM calls) | Bump to 600s. Local gemma4 entity extraction takes 30–120s per episode. |
| 11 | `jsonify({"dt_field": neo4j_datetime_object})` | Flask JSON serializer can't handle Neo4j DateTime | `_coerce()` helper with `isoformat()` conversion |
| 12 | SSH command with bare `?` in URL | zsh glob expansion error | Always quote URLs: `"http://host/api?x=y"` |
| 13 | SSH command with `===` label separators | zsh parsing error | Use plain labels: `echo -- LABEL --` |
| 14 | launchd plist without explicit `HOME`/`PATH` | Service started but couldn't find `uv` or venv Python | Add `EnvironmentVariables` dict with `PATH` including venv bin and `HOME=/Users/<user>` |

---

## Provider-registration pattern (the one that works)

Every external service Flyn talks to gets auth stored at `~/.openclaw/agents/main/agent/auth-profiles.json` under profile ID `<provider>:default`:

```
anthropic:default           — OAuth token (seed install; not used day-to-day)
openai-codex:...@gmail.com  — OAuth access+refresh (PRIMARY; flat-rate subscription)
gemini:default              — Google API key (for Gemini embedder)
google:default              — SAME Google API key (belt-and-suspenders; some call paths use this ID)
ollama:default              — literal token "local" (even though Ollama is local)
neo4j:default               — 32-char random password + uri + user
```

Rule: anything that writes to `openclaw.json` reads these from `auth-profiles.json` at runtime. No secret ever lives in openclaw.json.

---

## Cost model at steady state

- Codex OAuth subscription: **flat-rate $20-200/month** for user-facing turns
- Gemma 4 local heartbeat/background: **$0 per fire**
- Gemini embedding: free tier generous for a solo operator; expect **~$0-5/month** unless ingesting heavy PDF corpora
- Neo4j self-hosted: **$0**
- Lossless Claw summarization passes: use local gemma4; **$0 per summary**

Total predicted monthly cost: the Codex subscription + small Gemini embedding bill. ~$25-205/month total depending on which Codex plan.

---

## Install order that actually works (enforced by `deploy/install-flyn.sh`)

1. Verify OpenClaw 2026.4.15+, Ollama 0.21+, Docker, tmux, Homebrew available.
2. Pull `ollama/gemma4:e4b`.
3. Add `ollama:default` auth profile (token: "local").
4. Prompt operator for Gemini API key; store under `gemini:default` AND `google:default`.
5. Generate strong Neo4j password; start Neo4j Docker container; store `neo4j:default` profile.
6. Create Python venv; `pip install graphiti-core[google-genai] flask`.
7. Smoke-test Graphiti Python SDK end-to-end.
8. Deploy `flyn-graphiti-api.py`, launchd plist, start service, verify `/api/health`.
9. Install Lossless Claw plugin via `openclaw plugins install @martian-engineering/lossless-claw`.
10. Set OpenClaw config: `agents.defaults.heartbeat.model = ollama/gemma4:e4b`, `agents.defaults.models.ollama/gemma4:e4b = {}`, `agents.defaults.memorySearch.{provider,fallback,model}`, `agents.defaults.heartbeat.{isolatedSession,suppressToolErrorWarnings}`.
11. Restart gateway via launchctl; verify health.
12. Deploy workspace/*.md files to `~/.openclaw/workspace/`.
13. First-session bootstrap per BOOTSTRAP.md.

Every step is idempotent and safe to re-run.

---

## What we did NOT install (and why)

- **mem0** — schemaless ADD-only (v2.0 breaking change), weak temporal reasoning, open SQL/Cypher injection CVE (GHSA-5gv3-2fv6-jvhx). Graphiti wins on temporal.
- **Zep hosted** — external dependency, cloud, recurring cost. Graphiti self-hosted gives the same model locally.
- **Obsidian** — deferred. The curl + REST pattern gives the agent structured recall; human visibility can come later via Obsidian opening the workspace/memory/ folder as a vault. Not required for Flyn to function.
- **ACPX plugin enabled** — we enabled it briefly to test MCP path; reverted. ACPX is for ACP protocol clients (Codex CLI, etc.), not for `openclaw agent` turn-by-turn tool use.
- **`openclaw-mcp-bridge` community plugin** — uninstalled after proving it didn't close the MCP gap either.
- **Claude as primary** — reverted after Edge confirmed Codex works fine for this architecture.

---

## Open items

1. **MCP-to-agent integration investigation** — long-term concern, not blocking. If upstream OpenClaw fixes the MCP surface-to-tool-list wiring, we can migrate the REST wrapper back to MCP with minimal surface change. Until then, REST + curl is stable.
2. **Memory seeding** — Flyn's Graphiti KG starts empty. Seed the key architectural facts (who Ryan is, what Cora is, what the stack is) via a one-shot script in `deploy/seed/` so queries have something to return on day one.
3. **Cron job registration** — HEARTBEAT.md defines 5 pulses; `openclaw cron add` invocations for each are pending.
4. **Obsidian vault overlay** — optional, documented in Edge's pattern. Defer until Flyn has enough content to benefit from visual graph inspection.
5. **Skills rewrite in openclaw-base upstream** — the memory-options/* skills and deploy-memory-advanced.md in openclaw-base still reference MCP patterns that don't work. Update in a separate PR so the upstream library reflects reality.

---

## Files on 4C after this install

```
~/.openclaw/
├── openclaw.json                         (validated config, no invented keys)
├── agents/main/agent/
│   └── auth-profiles.json                (6 profiles: anthropic, codex, gemini, google, ollama, neo4j)
├── extensions/
│   └── lossless-claw/                    (plugin bundle, contextEngine slot)
├── scripts/flyn/                         (cron/heartbeat scripts — installed by register-flyn-crons.sh)
│   ├── common.sh
│   ├── morning-digest.sh
│   ├── memory-autosave.sh
│   ├── health-check.sh
│   ├── memory-rollup.sh
│   ├── model-drift.sh
│   └── gemma4-warm-at-boot.sh
├── logs/                                 (heartbeat logs + cron-<label>.{log,err} + kg-failed/)
└── workspace/
    ├── IDENTITY.md, SOUL.md, HEARTBEAT.md, MEMORY.md
    ├── AGENTS.md, USER.md, TOOLS.md, BOOTSTRAP.md
    ├── memory/                           (daily markdown + weekly/ + structured/)
    ├── kg/
    │   └── flyn-graphiti-api.py          (Flask REST wrapper, byte-identical to repo copy)
    └── memory/structured/
        ├── graphiti-venv/                (Python venv, pinned via requirements-lock.txt)
        ├── graphiti-repo/                (upstream clone, reference only — vestigial from MCP experiment)
        ├── neo4j/{data,logs}/            (Docker volumes)
        ├── requirements-lock.txt         (source-of-truth for venv pins; also at deploy/kg/ in repo)
        ├── flyn-graphiti-launch.sh       (vestigial MCP stdio launcher, unused; safe to delete)
        └── flyn-graphiti-config.yaml     (vestigial MCP config, unused; safe to delete)

~/Library/LaunchAgents/
├── ai.flyn.graphiti-api.plist                    (Flask REST on :8100, KeepAlive)
├── ai.flyn.pulse.morning-digest.plist            (weekdays 07:00)
├── ai.flyn.pulse.memory-autosave.plist           (hourly 06:00–23:00)
├── ai.flyn.pulse.health-check.plist              (daily 22:00)
├── ai.flyn.pulse.memory-rollup.plist             (Sundays 20:00)
├── ai.flyn.pulse.model-drift.plist               (Sundays 21:00)
└── ai.flyn.gemma4-warm-at-boot.plist             (RunAtLoad, one-shot, primes gemma4:e4b)
```

**Vestigial MCP scaffolding** (`graphiti-repo/`, `flyn-graphiti-launch.sh`,
`flyn-graphiti-config.yaml`) is left on disk from the abandoned MCP experiment.
Nothing launches it; it's safe to delete but not included in `install-flyn.sh`
cleanup since it's ~30 MB and causes no runtime issue.

---

## One-line summary

**MCP was the wrong abstraction; REST + curl from the exec tool is the working pattern, matching Edge's production architecture.** Everything else (Lossless Claw, Gemma 4, Gemini embeddings, Codex primary) worked as designed once we stopped fighting the MCP integration.
