---
name: claude-p-oauth-refresh-fallback
description: Headless `claude -p` can lose its OAuth session under concurrent use or token-refresh races. ANTHROPIC_API_KEY fallback in worker env keeps the worker functional and the operator's interactive Claude Code session stable.
type: reference
---

# `claude -p` OAuth refresh fallback

claude-code#28827 documents that headless `claude -p` invocations can fail OAuth refresh in long runs or under concurrent access. Worse, the refresh failure can yank the operator's interactive Claude Code session — the same `~/.claude/.credentials.json` is shared.

Mitigation in `backends/claude_p.py`:

1. If `ANTHROPIC_API_KEY` is set in env, pass it through to the worker subprocess.
2. If not in env, look up `anthropic:default` in `~/.openclaw/agents/main/agent/auth-profiles.json` and pass that through.
3. `claude -p` itself decides whether to use OAuth or API-key auth — API-key takes precedence when both are present.

Cost note: API-key auth is per-token billed, not subscription. For Cora's MVP usage (~$0.45 per task), this is negligible — but if Phase 2 dev workflow ships and scales to dozens of tasks per day, switch back to subscription OAuth and accept the occasional refresh failure.
