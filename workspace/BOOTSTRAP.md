# BOOTSTRAP — Flyn

Loaded **only on the first session after deploy**. Rename to `BOOTSTRAP-completed-YYYY-MM-DD.md` after the checklist is done so it doesn't load again.

Assumes `deploy/install-flyn.sh` from the flyn-agent repo has already been run. This file is the AGENT-SIDE checklist that follows.

---

## First-session checklist

1. **Confirm identity.** Read IDENTITY.md + SOUL.md. Introduce self to Ryan in character: `"Flyn online. CEO of 4C. Codex 5.4 primary, local Gemma 4 for background. Graphiti REST on :8100. Memory stack live."` Ask: "Right configuration?"

2. **Verify operator profile.** Read USER.md. Confirm:
   - Timezone (default: America/Denver — correct?)
   - Telegram bot handle (what was actually created?)
   - Hard nos list — any additions for this deployment?

3. **Probe OpenClaw health + tool access.**
   ```bash
   openclaw health
   openclaw doctor
   openclaw models auth list
   openclaw channels list
   ```
   Report which tools are green and which need attention.

4. **Verify structured memory stack.**
   ```bash
   # REST API up?
   curl -s http://localhost:8100/api/health
   # Expect: {"group":"flyn","neo4j":"connected","status":"ok"}
   
   # Neo4j Docker container?
   docker ps --filter name=flyn-neo4j --format "{{.Names}} {{.Status}}"
   
   # Gemma 4 pulled?
   ollama list | grep gemma4:e4b
   
   # Lossless Claw in contextEngine slot?
   openclaw config get plugins.slots.contextEngine
   # Expect: lossless-claw
   
   # launchd service for the REST API?
   launchctl print gui/$(id -u)/ai.flyn.graphiti-api 2>&1 | grep state
   # Expect: state = running
   
   # Heartbeat routing to local Gemma?
   openclaw config get agents.defaults.heartbeat.model
   # Expect: ollama/gemma4:e4b
   ```

5. **Seed first Graphiti episode** so the KG isn't empty.
   ```bash
   curl -sS -X POST http://localhost:8100/api/episode \
     -H 'Content-Type: application/json' \
     -d '{"body": "Flyn deployed and bootstrapped on Mac Mini 4C on YYYY-MM-DD by Ryan. Primary: Codex 5.4 via OAuth. Background: local gemma4:e4b via Ollama. Structured memory: Graphiti + Neo4j via REST on localhost:8100. Context engine: Lossless Claw. Embeddings: Gemini (gemini-embedding-001).", "name": "flyn-deploy-bootstrap"}'
   # Blocks 30-120s while entity extraction runs; returns {"ok": true, ...}
   ```
   Confirm retrievable: `curl -sS 'http://localhost:8100/api/search?q=Flyn+deployed'`

6. **Create the first markdown memory entry.**
   ```
   workspace/memory/YYYY-MM-DD.md:
   Flyn deployed today on Mac Mini 4C.
   Initial probe results: [from step 3]
   Memory stack health: [from step 4]
   First Graphiti episode: flyn-deploy-bootstrap
   Open items: [anything that needed manual intervention]
   ```

7. **Confirm heartbeat cadence.** Read HEARTBEAT.md with Ryan. For each pulse, confirm:
   - Timezone is right
   - The channel it posts to exists (`#flyn-briefing`, `#flyn-alerts`)
   - The Graphiti REST is reachable for auto-ingest

8. **Register cron jobs** per HEARTBEAT.md (skip if already done by `install-flyn.sh`):
   ```bash
   openclaw cron add --name flyn-morning-digest --cron "0 7 * * 1-5" --command "~/.openclaw/scripts/morning-digest.sh"
   openclaw cron add --name flyn-hourly-memory-save --cron "0 6-23 * * *" --command "~/.openclaw/scripts/memory-autosave.sh"
   openclaw cron add --name flyn-daily-health --cron "0 22 * * *" --command "~/.openclaw/scripts/health-check.sh"
   openclaw cron add --name flyn-weekly-rollup --cron "0 20 * * 0" --command "~/.openclaw/scripts/memory-rollup.sh"
   openclaw cron list
   ```

9. **Smoke-test the curl-from-exec pattern.** This verifies Flyn can actually use structured memory live (not just via setup scripts). Ask Flyn:
   > "Record that we just completed the Flyn bootstrap by posting an episode via the REST API, then confirm with a search."
   
   Watch for Neo4j episode count to increment: `docker exec flyn-neo4j cypher-shell -u neo4j -p "$(python3 -c 'import json; print(json.load(open("~/.openclaw/agents/main/agent/auth-profiles.json".replace("~", __import__("os").path.expanduser("~"))))["profiles"]["neo4j:default"]["token"])')" 'MATCH (n:Episodic {group_id:"flyn"}) RETURN count(n) AS eps'`

10. **Ask about preferences.** Anything to adjust in:
    - AGENTS.md approval gates?
    - SOUL.md voice (too dry? not dry enough?)
    - HEARTBEAT.md cadence?
    - Additional hard nos for USER.md?

11. **Mark bootstrap complete.**
    ```bash
    mv ~/.openclaw/workspace/BOOTSTRAP.md ~/.openclaw/workspace/BOOTSTRAP-completed-$(date +%Y-%m-%d).md
    ```
    Flyn proposes this rename; Ryan confirms before it happens.

---

## If bootstrap is interrupted

Leave this file in place. Record progress in `workspace/memory/YYYY-MM-DD.md`. On resume, skip completed items.

## Do NOT put here

- Permanent config — that's `openclaw.json` or template files
- Long-term memory — that's `MEMORY.md` or Graphiti (via `/api/episode`)
- Recurring tasks — that's `HEARTBEAT.md` / `openclaw cron`
