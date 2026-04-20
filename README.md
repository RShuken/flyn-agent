# flyn-agent

Private workspace + config for **Flyn** — Ryan's CEO of Mac Mini 4C. Flyn owns the machine end-to-end: strategy, orchestration, execution, interactive turns. Peers with other agents (Rel elsewhere, future deployments) over the OAC gateway as equals — no subordinate, no principal.

## What this repo is

A Flyn-specific overlay on top of `openclaw-base` (upstream library):

- `workspace/` — Flyn's IDENTITY / SOUL / HEARTBEAT / MEMORY / AGENTS / USER / TOOLS / BOOTSTRAP, ready to copy into `~/.openclaw/workspace/` on 4C
- `openclaw.json` — Flyn's model stack (Codex GPT-5.4 primary, local Gemma 4 for background) + memory config (Lossless Claw + Gemini 2 embeddings with EmbeddingGemma local fallback + mem0)
- `deploy/install-memory-stack.md` — ordered runbook for installing the memory layers on 4C
- `skills/`, `templates/`, `audit/`, `install/` — inherited from `openclaw-base` upstream; Flyn uses these as reference

## How it relates to openclaw-base

- `openclaw-base` = public source-of-truth library (skills, templates, audit research)
- `flyn-agent` = private Flyn-specific config on top

The `upstream` git remote points to `openclaw-base`. To pull library updates later:

```bash
git fetch upstream
git merge upstream/main    # review conflicts in workspace/ and openclaw.json
```

## Deploying Flyn on 4C

See `deploy/install-memory-stack.md` for the memory stack install order. High-level deploy:

1. SSH to 4C: `ssh 4c-raw` (needs `zsh -l -c` wrapper per `feedback_ssh_commands`)
2. Backup existing workspace: `mv ~/.openclaw/workspace ~/.openclaw/workspace.bak-$(date +%Y-%m-%d)`
3. Clone flyn-agent on 4C (or rsync from laptop)
4. Copy `workspace/*` → `~/.openclaw/workspace/`
5. Copy `openclaw.json` → `~/.openclaw/openclaw.json`
6. Run the memory stack install: `deploy/install-memory-stack.md` steps 0-10
7. Verify: `openclaw health && openclaw doctor && openclaw models auth list`
8. First session: Flyn processes `BOOTSTRAP.md` — walk through the checklist with Ryan
9. Rename `BOOTSTRAP.md` → `BOOTSTRAP-completed-YYYY-MM-DD.md` once checklist is done

## Architecture summary

```
                 ┌─────────────────────┐
                 │  Ryan (operator)    │
                 └──────────┬──────────┘
                            │ Telegram / direct
                            ▼
       ┌──────────────────────────────────────────┐
       │   Flyn — CEO of Mac Mini 4C              │
       │   ├── Codex GPT-5.4 primary (OAuth sub)  │
       │   ├── Gemma 4 local (heartbeats/cron)    │
       │   ├── Lossless Claw context engine       │
       │   ├── Gemini 2 embeddings → sqlite-vec   │
       │   ├── mem0 structured memory             │
       │   └── EmbeddingGemma local fallback      │
       └─────────┬───────────────────────┬────────┘
                 │ spawns                │ peers
                 ▼                       ▼
       ┌──────────────┐         ┌──────────────────┐
       │ Sub-agents   │         │ Other agents     │
       │ (specialists │         │ (Rel elsewhere,  │
       │  Flyn spawns │         │  via OAC gateway,│
       │  on demand)  │         │  as equals)      │
       └──────────────┘         └──────────────────┘
```

Flyn owns 4C end-to-end. Sub-agents are tools Flyn spawns. Other agents are peers Flyn coordinates with. Everyone respects Ryan's approval gates (`workspace/AGENTS.md`).

## Secrets

- Never committed. All secrets (Codex OAuth token, Gemini API key, Telegram bot token) live in `~/.openclaw/agents/main/agent/auth-profiles.json` on 4C, which is gitignored from this repo and from the OpenClaw workspace.
- To re-auth: `openclaw models auth login --provider <name>`

## Upstream

`openclaw-base` (RShuken/openclaw-base) — the public library. Skills, audit research, and templates flow from there.
