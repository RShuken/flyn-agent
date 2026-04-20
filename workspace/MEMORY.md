# MEMORY — Flyn (Hot tier)

**SECURITY GATE:** This file is loaded ONLY in main-session or trusted DM context. NEVER in group chat or sub-agent context. See `AGENTS.md` session-type routing.

Target: under 200 lines. When it grows beyond that, the oldest entries roll to `workspace/memory/warm/YYYY-WW.md` (weekly) and ultimately to `workspace/memory/cold/YYYY-MM.md` (monthly). See `../skills/memory-options/community-patterns.md` for the Hot/Warm/Cold pattern.

---

## Index of long-term memory files

*(This index is maintained by the weekly rollup pulse. Add pointers here as memory grows.)*

- `workspace/memory/warm/` — daily files older than 1 week
- `workspace/memory/cold/` — weekly rollups older than 1 month
- `workspace/memory/structured/` — mem0 entity + relationship store (queried via `openclaw memory search`, not read as files)

## Active context (refreshed on heartbeat)

- **Current agent state:** fresh install on Mac Mini 4C, first boot pending
- **Paired with:** Rel (via OAC gateway) — Rel is primary, Flyn is execution
- **Primary channel:** Telegram `@FlynBot` (TBD — confirm in BOOTSTRAP), topic routing per TOOLS.md

## Standing preferences (pinned — don't roll off)

- Local-first for background; frontier cloud only for user-chat turns
- Codex OAuth subscription is the cost path — never pay-per-token default
- No auto-Keychain migration under launch-agent setup (64-hour outage lesson)
- Research-first + primary sources + evidence-based completion
- Always local → dev → prod (never auto-merge without go-ahead)

## Recent activity

*(Populated by `hourly-memory-auto-save` heartbeat pulse starting after first session.)*
