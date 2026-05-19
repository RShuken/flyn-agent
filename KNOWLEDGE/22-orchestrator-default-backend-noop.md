---
name: orchestrator-default-backend-noop
description: The orchestrator's default backend is "noop" to prevent OAuth contention. Set FLYN_DEFAULT_BACKEND=codex-exec (or claude-p, with caveats) to enable real LLM workers.
type: reference
---

# Orchestrator default backend is noop to avoid OAuth contention

The flyn-orchestrator at `:8300` dispatches work to a pluggable `WorkerBackend`. Originally
`claude-p` was the hardcoded default. Every worker invocation used the same `~/.claude/.credentials.json`
OAuth token as Ryan's interactive Claude Code sessions. During heavy task runs, OAuth refresh races
caused interactive sessions to drop — Ryan got logged out mid-conversation. See KNOWLEDGE/17 for the
underlying refresh-race mechanics and KNOWLEDGE/21 for the token-prefix discrimination issue.

The fix (PR A10, 2026-05-19) makes `noop` the default. `NoopBackend` satisfies the `WorkerBackend`
protocol, returns `exit_code=0`, writes a one-line JSONL capture file (`{"backend":"noop",...}`), and
costs nothing. The orchestrator's full state machine (routing, audit, approval gates) runs correctly
without any LLM call. Switching to a real backend is a single env-var change.

## Backend registry

| name | what it does | when to use |
|---|---|---|
| `noop` | No LLM call; safe smoke / pipeline test | Default; always safe |
| `codex-exec` | `codex exec --json` — ChatGPT Plus/Pro OAuth or `OPENAI_API_KEY` | Preferred real backend |
| `claude-p` | `claude -p` — Claude Code OAuth (shared with interactive session) | Explicit opt-in only |

## How to swap

Set `FLYN_DEFAULT_BACKEND` in the launchd plist or shell before starting the orchestrator:

```bash
# Codex (separate OAuth / API key — no contention):
export FLYN_DEFAULT_BACKEND=codex-exec

# Claude (NOT recommended — competes with interactive Claude Code sessions):
export FLYN_DEFAULT_BACKEND=claude-p
```

The plist template at `deploy/orchestrator/ai.flyn.orchestrator.plist.template` ships with
`FLYN_DEFAULT_BACKEND=noop`. Edit that key before loading the plist to switch backends.

## Cross-references

- KNOWLEDGE/17: `claude-p` OAuth refresh fallback (how the contention manifests)
- KNOWLEDGE/21: OAuth vs API-key token discrimination (`sk-ant-oat-*` vs `sk-ant-api-*`)
