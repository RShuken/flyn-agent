# MEMORY — Chet (Hot tier)

**SECURITY GATE:** This file is loaded ONLY in main-session or trusted DM context. NEVER in group chat, public Google Chat space, or sub-agent context. See `AGENTS.md` session-type routing.

Target: under 200 lines. Older entries roll to `workspace/memory/warm/YYYY-WW.md` (weekly) and ultimately `workspace/memory/cold/YYYY-MM.md` (monthly). See upstream `flyn-agent` `skills/memory-options/community-patterns.md` for the Hot/Warm/Cold pattern.

---

## Index of long-term memory files

*(Maintained by the weekly rollup pulse. Add pointers here as memory grows.)*

- `workspace/memory/warm/` — daily files older than 1 week
- `workspace/memory/cold/` — weekly rollups older than 1 month
- `workspace/memory/structured/` — Graphiti + Neo4j (queried via REST on `localhost:8100`, not read as files)

## Active context (refreshed on heartbeat)

- **Current agent state:** fresh install on Tune Outdoor's Mac on 2026-05-08, first boot pending
- **Mandate:** project-management EA for the Tune Outdoor team. Coordination, recurring ops, task tracking.
- **Primary channel:** Telegram bot (TBD — confirm in BOOTSTRAP). Google Chat is the long-term primary, pending integration build.

## Standing preferences (pinned — don't roll off)

- Multi-user environment — discretion across team-member threads
- Local-first for background work; frontier cloud only for live user turns
- OpenAI Codex OAuth subscription is the cost path — never pay-per-token default
- No auto-Keychain migration under launch-agent setup (64-hour outage lesson inherited from upstream Flyn deploy)
- Sandbox / test → live (never auto-modify production data without go-ahead)
- Evidence-based reporting (no "should be done")

## Recent activity

*(Populated by `hourly-memory-auto-save` heartbeat pulse starting after first session.)*
