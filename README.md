# flyn-agent

Private deploy repo for **Flyn** — Ryan's CEO of Mac Mini 4C.

Fully validated end-to-end on 2026-04-21. This repo carries:
- The workspace files that drop into `~/.openclaw/workspace/` (persona, rules, tools, heartbeat, bootstrap)
- An idempotent `deploy/install-flyn.sh` that stands Flyn up on a fresh Mac Mini
- The REST-wrapper code + launchd plist for Graphiti structured memory
- A `POSTMORTEM-2026-04-21.md` documenting everything that didn't work and why the repo is shaped the way it is

Read the postmortem FIRST before changing deploy scripts or workspace files. It captures the expensive lessons.

---

## Architecture (as shipped)

```
Ryan (operator, via Telegram + direct)
   │
   ▼
Flyn — Mac Mini 4C (OpenClaw 2026.4.15)
   ├── Primary:          openai-codex/gpt-5.4  (OAuth subscription, flat-rate)
   ├── Background:       ollama/gemma4:e4b     (local Metal, ~11 GB when hot, idle-unloads)
   ├── Context engine:   Lossless Claw plugin  (plugins.slots.contextEngine)
   ├── Embeddings:       Gemini API (gemini-embedding-001)  + local EmbeddingGemma fallback
   ├── Vector store:     sqlite-vec (built-in)
   └── Structured KG:    Graphiti + Neo4j  behind  Flask REST @ localhost:8100
                         ↑ called by agent via curl from exec tool (NOT via MCP)
   │
   ▼ spawns on demand
Sub-agents (for focused specialist tasks)
```

**Why REST + curl instead of MCP:** every MCP registration path we tried on OpenClaw 2026.4.15 resulted in the agent hallucinating tool calls. The exec/shell tool surfaces `curl` to the model correctly. Edge (Dan Caruso's production agent on the same OpenClaw version) uses the same REST + curl pattern. Full investigation in `POSTMORTEM-2026-04-21.md`.

---

## Repo layout

```
flyn-agent/
├── README.md                        ← this file
├── POSTMORTEM-2026-04-21.md         ← what worked, what didn't, why
├── workspace/                       → deploys to ~/.openclaw/workspace/
│   ├── IDENTITY.md  SOUL.md  USER.md        (persona + operator profile)
│   ├── AGENTS.md    HEARTBEAT.md  MEMORY.md (rules, pulses, hot-tier memory)
│   ├── TOOLS.md                              (what Flyn has; curl patterns for KG)
│   └── BOOTSTRAP.md                          (first-session checklist, agent-side)
├── deploy/
│   ├── install-flyn.sh              ← idempotent end-to-end deploy script
│   ├── kg/
│   │   └── flyn-graphiti-api.py     Flask REST wrapper (Graphiti + Neo4j → :8100)
│   ├── launchd/
│   │   └── ai.flyn.graphiti-api.plist.template  ({{HOME}} templated; install-flyn.sh renders)
│   └── deprecated/                  (superseded docs, kept for audit trail)
└── (inherited from openclaw-base upstream: skills/, templates/, audit/, install/, catalog.json)
```

## Deploy

On the target Mac Mini:

```bash
git clone git@github.com:RShuken/flyn-agent.git
cd flyn-agent
./deploy/install-flyn.sh
```

The script is idempotent — safe to re-run. It:
1. Verifies prerequisites (OpenClaw 2026.4.15+, Ollama 0.21+, Docker, tmux, Homebrew, Python 3.10+).
2. Pulls `gemma4:e4b` (9.6 GB).
3. Bootstraps auth profiles: `ollama:default`, prompts for Gemini key, generates Neo4j password.
4. Starts Neo4j 5.26 in Docker with 1 GB heap cap, persistent volumes under the workspace.
5. Creates the Graphiti Python venv + installs `graphiti-core[google-genai] + flask`.
6. Deploys the REST wrapper + launchd plist; starts the service.
7. Installs Lossless Claw plugin (`@martian-engineering/lossless-claw`).
8. Additively sets OpenClaw config for heartbeat + memorySearch + models allowlist.
9. Rsyncs `workspace/*.md` into `~/.openclaw/workspace/`.
10. Restarts the gateway.

On first session after install, Flyn runs the `workspace/BOOTSTRAP.md` checklist interactively with Ryan.

## Upstream relationship

- **`openclaw-base`** (public, `RShuken/openclaw-base`) is the shared library — skill definitions, audit research, canonical templates.
- **`flyn-agent`** (private, this repo) is the Flyn-specific deploy.
- Git remote `upstream` points to openclaw-base. Pull library updates with `git fetch upstream && git merge upstream/main` — review conflicts in `workspace/` and `deploy/`.

## Secrets

Never committed. All keys and tokens live in `~/.openclaw/agents/main/agent/auth-profiles.json` on the deploy host. The install script reads from that file at runtime and prompts for missing keys. `deploy/install-flyn.sh` explicitly avoids embedding any secret in config files it writes.

Required profiles after install:
- `openai-codex:<email>` — OAuth (installed by `openclaw models auth login --provider openai-codex`)
- `gemini:default` + `google:default` — same Gemini API key, both profile IDs needed
- `ollama:default` — literal `"local"` token
- `neo4j:default` — auto-generated during install; see uri/user/token fields

## What's NOT in this deploy (deliberate choices, see POSTMORTEM for rationale)

- No mem0 (Graphiti wins on temporal + schema evolution + no open CVE)
- No OpenClaw MCP registration for the structured memory (agent-turn surface is broken for MCP in this version)
- No Claude as primary (Codex OAuth is the cost-efficient path; Edge confirms this works)
- No Obsidian (optional future overlay)
- No `openclaw.json` committed in this repo (the live file on the deploy host carries other tooling's auth and must be edited additively)

---

## Status

| Phase | State |
|-------|-------|
| 1. Fork from openclaw-base + Flyn persona drafted | DONE |
| 2. Workspace files (IDENTITY/SOUL/HEARTBEAT/etc) | DONE |
| 3. Live install on 4C (Lossless Claw, Gemma 4, Gemini, Neo4j, Graphiti REST, launchd) | DONE 2026-04-21 |
| 4. Agent successfully uses structured KG via curl-from-exec | PROVEN 2026-04-21 |
| 5. Memory seeding + cron registration | PENDING (BOOTSTRAP.md steps 5, 8) |
| 6. Daily use + refinements based on what Flyn learns | ongoing |
