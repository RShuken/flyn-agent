# KNOWLEDGE — portable lessons learned deploying OpenClaw agents on Apple Silicon

These are the architectural gotchas, working recipes, and non-obvious failure modes captured during Flyn's install on Mac Mini 4C (and adjacent deployments). They're written in terse feedback-memo form — one concept per file, with "why" + "how to apply" so the reasoning survives even when specific commands drift.

**Read order for a fresh operator:** 01 → 02 → 03 → 04 → 08 → 09 → 10 → skim the rest. The first 4 are universal; 08–10 explain why the structured-memory layer is shaped the way it is.

**Original lineage:** extracted from durable Claude Code memory files (`~/.claude/projects/.../memory/feedback_*.md`). Ported into this repo so anyone cloning gets the lessons without needing access to the originating agent's private memory store.

---

## Index

### Foundational principles (read first)

- [01-probe-schema-before-writing.md](01-probe-schema-before-writing.md) — never write openclaw.json by hand; dump `openclaw config schema` and use `openclaw config set` for additive edits.
- [02-local-background-routing.md](02-local-background-routing.md) — cost-optimal routing shape: cloud frontier for user-chat turns only; heartbeat/cron/embeddings go local.
- [11-agents-are-peers-not-subordinate.md](11-agents-are-peers-not-subordinate.md) — "paired with X" does not imply X is the boss. Each agent owns its own domain end-to-end.

### Platform landmines (know these before you install)

- [03-node-25-tls-fingerprint-block.md](03-node-25-tls-fingerprint-block.md) — Homebrew Node 25 TLS fingerprint gets blocked by Cloudflare, breaking Codex LLM calls. Use tarball Node 22 LTS.
- [04-postmortem-ian-14hr-outage.md](04-postmortem-ian-14hr-outage.md) — 14-hour client outage. Three-layer root cause (Node 25 + OpenClaw 2026.4.14 models.json corruption + missing channels after restore). The most expensive Flyn-adjacent incident on record.
- [14-macos-nonadmin-tarball-node.md](14-macos-nonadmin-tarball-node.md) — non-admin macOS clients can't install Homebrew; use direct Node tarball to `~/.local/node`.

### Models + routing (the one fight that burned half the session)

- [05-ollama-tool-capable-model.md](05-ollama-tool-capable-model.md) — Gemma 3 at 4B lacks tool-call support → HTTP 400 → falls back to cloud. Use a tool-capable model.
- [06-gemma4-heartbeat-recipe.md](06-gemma4-heartbeat-recipe.md) — proven working config for local heartbeat on gemma4:e4b. Also documents the `openclaw capability model run --model` CLI quirk (it lies about routing).
- [07-gemini-google-auth-both-profiles.md](07-gemini-google-auth-both-profiles.md) — store the Gemini API key under BOTH `gemini:default` and `google:default` profile IDs. OpenClaw's embedding provider ID is `gemini`; auth lookup uses `google`. Both entries required.

### Structured memory architecture (the main event)

- [08-graphiti-neo4j-recipe.md](08-graphiti-neo4j-recipe.md) — validated install: Neo4j in Docker, Graphiti Python venv, local LLM for entity extraction, Gemini for embeddings.
- [09-mcp-agent-turn-gap-investigation.md](09-mcp-agent-turn-gap-investigation.md) — the investigation that burned 4+ hours. MCP-registered tools don't surface to the agent's tool list at turn time on OpenClaw 2026.4.15. Six registration paths tried; all hallucinated.
- [10-rest-pattern-the-working-fix.md](10-rest-pattern-the-working-fix.md) — the pattern that actually works: wrap Graphiti in a local Flask REST, call via curl from the exec shell tool. Same pattern production operators (e.g. Edge/Caruso engagement) use.

### Operational micro-gotchas (cheap to ignore, expensive to repeat)

- [12-ssh-zsh-wrapper-for-openclaw-cli.md](12-ssh-zsh-wrapper-for-openclaw-cli.md) — SSH to a deploy host needs `zsh -l -c` wrapper; `===` in SSH commands breaks (zsh parses as comparison operator).
- [13-tmux-wrap-long-running-installs.md](13-tmux-wrap-long-running-installs.md) — wrap `ollama pull`, `brew upgrade`, big installs in `tmux new-session -d` to survive the ~10-min PTY timeout common on remote-exec shells.

---

## How these were captured

Each file corresponds to a specific moment this session (2026-04-20 through 2026-04-21) where we hit a failure, fixed it, and wrote down the finding before moving on. "Research-first" on the way in, "postmortem-first" on the way out. The companion document is `../POSTMORTEM-2026-04-21.md` at the repo root — it's the narrative; the files here are the reference index.

## How to keep this alive

When you hit a new rabbit hole during a future deploy, file a new numbered entry in this directory following the same shape:

```markdown
---
name: Short sentence — what the rule is
description: One-liner for skim readers
type: feedback
---

[Problem statement — 1-2 sentences]

**Why:** [the incident that taught this, with date]

**How to apply:** [concrete steps / commands / invariants]
```

Date every one. Future-you will want to know if a fact is still load-bearing or has been patched upstream.
