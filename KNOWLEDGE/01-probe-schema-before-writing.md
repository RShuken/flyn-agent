---
name: Probe live OpenClaw config schema before writing openclaw.json
description: Never design openclaw.json keys from assumptions or templates. Read the live working file on the target host first, mirror its shape, additively merge new keys.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
When designing or modifying `~/.openclaw/openclaw.json` on a deployment target, **always read the existing live file FIRST** and use it as the schema reference. Do not invent keys based on what would be "logical" or what other systems do.

**Why:** 2026-04-20 deploy to 4C — I wrote a Flyn `openclaw.json` from scratch containing keys I assumed should exist (`memory.embedding`, `memory.contextEngine`, `memory.vectorStore`, `memory.structured`, `agents.defaults.models.X.contextTokens`, `agents.defaults.models.X.kind`, root-level `providers`, `_comment`, `agents.defaults.heartbeat._comment`). OpenClaw 2026.4.15 rejected all of them ("Unrecognized key"). I also clobbered the live file containing real API keys (`notion`, `openai-whisper-api`, `goplaces`, `sag`) and tool/plugin/hook config that took effort to set up. Backup made recovery clean, but the deploy needed a rollback + redesign.

**How to apply:**
- Before generating openclaw.json content for any target, SSH (or otherwise) and `cat` the existing file. Use it as the schema spine.
- Treat any new config addition as an **additive merge** — preserve the existing `skills`, `plugins`, `hooks`, `tools`, `meta`, `wizard`, `auth`, etc. Only ADD new keys (or override specific ones intentionally).
- Memory backend config (Lossless Claw, mem0) likely lives via `openclaw skills install <name>` and `openclaw config set …`, not as direct openclaw.json keys. Verify before assuming.
- API keys belong in `~/.openclaw/agents/<id>/agent/auth-profiles.json` per `_deploy-common.md` "Secret storage" — though some `skills.entries.<skill>.apiKey` plain-text usage is observed in current 4C config. That existing pattern is a separate cleanup concern.
- Schema check command: `openclaw health` will report invalid keys; `openclaw doctor` will run with best-effort but flag unknowns.
