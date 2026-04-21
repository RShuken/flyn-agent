---
name: openclaw-local-background-routing
description: Cost-optimal OpenClaw model routing — user chats go to cloud frontier model, everything else (heartbeats, crons, embeddings) goes to local Ollama. Default to this on every install.
type: feedback
originSessionId: 0eaf92c3-b027-400b-b564-f087026dba75
---
## Route background work to local models; keep frontier models for user chat only

**Why:** Ian Ferguson's install on 2026-04-18 surfaced that his scheduled jobs had been silently spending ~2.7M tokens/month on background work (memory re-indexing, daily ingestion, nightly backup). None of those tasks needed a frontier model — they're summarization/embedding work that a 2B-param local model handles just as well.

**How to apply:** On every new OpenClaw install, configure this routing shape by default:

```
┌──────────────────────────────┬──────────────────────────────────────┐
│ Use case                     │ Model                                │
├──────────────────────────────┼──────────────────────────────────────┤
│ User chats (real-time)       │ openai-codex/gpt-5.4 (or equivalent) │
│ Fallbacks                    │ ..., ollama/<local-model>            │
│ Heartbeat (every 30 min)     │ ollama/<local-model>                 │
│ Scheduled crons (daily/etc)  │ ollama/<local-model>                 │
│ Memory embeddings            │ ollama/nomic-embed-text (or similar) │
└──────────────────────────────┴──────────────────────────────────────┘
```

Only the real-time user-facing path pays cloud tokens. Everything else is local + free + outage-resilient.

### Setup checklist

1. **Pull a small local chat model** — `ollama pull gemma4:e2b` (2.3B effective, ~7 GB on disk) or `qwen2.5:3b` / `llama3.2:3b` for alternatives. Use `tmux new-session -d -s gpull <cmd>` to survive the PTY 10-min timeout.
2. **Ensure Ollama is a current version** — `brew upgrade ollama` first if user's Ollama is older than the model's release date. The "https://ollama.com/download" cryptic error = version too old.
3. **Add ollama auth profile** — OpenClaw requires an auth entry even though Ollama ignores it. Quick write:
   ```python
   python3 -c 'import json; p="/Users/X/.openclaw/agents/main/agent/auth-profiles.json"; d=json.load(open(p)); d["profiles"]["ollama:default"]={"type":"api_key","provider":"ollama","apiKey":"ollama-local"}; json.dump(d,open(p,"w"),indent=2)'
   ```
4. **Add to fallback list for whitelisting** — `openclaw models fallbacks add ollama/<model>` (this also allowlists it for CLI `--model` overrides).
5. **Set heartbeat** — `openclaw config set agents.defaults.heartbeat.model ollama/<model>`.
6. **Flip existing crons** — `openclaw cron list` to find them, `openclaw cron edit <id> --model ollama/<model>` for each. **Check for stale anthropic refs** from old Foundation Layer deploys.
7. **Restart gateway** — `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`.
8. **Verify end-to-end** — `openclaw infer model run --model ollama/<model> --prompt "reply OK"` should return OK via `provider=ollama`.

### Why a local model is fine for this work

- **Heartbeats** = "are you alive?" — 1-sentence response, any model works
- **Memory re-indexing** = summarize day's notes, generate embeddings — no reasoning needed
- **Daily ingestion** = classify/tag new documents — classification task, local models are great
- **Backup summarization** = condensed daily digest — cheap and local

### What NOT to route locally (keep on cloud frontier)

- **User-facing chat turns** — personality + reasoning quality matters, users notice the difference
- **Tool-use loops** — if the bot uses complex tools, frontier models coordinate them better
- **Code generation in response to user** — local models noticeably weaker at this
- **Critical summarization where quality is visible to user** — keep cloud

### Cron-model gotcha for context

When you remove a provider (like we did with Anthropic for Ian), **cron jobs can still reference the removed provider** in their per-job `model` field — which is stored separately in `~/.openclaw/cron/<id>.json`, NOT in `openclaw.json`. These stale refs silently fall through to fallbacks, which works but burns tokens on the fallback provider unnecessarily. Always audit `openclaw cron list` after any provider removal.

**Cross-reference:** `project_ian_ferguson_install_state.md` has the full set of commands that worked end-to-end for Ian. Apply the same sequence to Brian, Dan Caruso, Marshall Mosher, Paul Revas, anyone else running OpenClaw with scheduled jobs. Josh Vaughn's consulting templates should include this routing as default.
