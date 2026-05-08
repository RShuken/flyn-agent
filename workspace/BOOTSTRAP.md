# BOOTSTRAP — Chet (Tune Outdoor)

Loaded **only on the first session after deploy**. Rename to `BOOTSTRAP-completed-YYYY-MM-DD.md` after the checklist is done so it doesn't load again.

Assumes `deploy/install-flyn.sh` from the `tune-outdoor` branch of `flyn-agent` has already been run on Tune Outdoor's Mac. This file is the AGENT-SIDE checklist that follows.

> **Service-name reminder for the operator:** `install-flyn.sh` provisions services with `flyn-*` names (`flyn-neo4j` Docker container, `ai.flyn.*` launchd labels, `flyn-graphiti-api` log paths, Graphiti `group_id="flyn"`). The agent's identity is **Chet**; the service-layer names are upstream-inherited and intentional. Renaming the service layer is a follow-up — don't try to rename at runtime.

---

## First-session checklist

1. **Confirm identity.** Read IDENTITY.md + SOUL.md. Introduce self in character to whoever is at the console (likely Ryan + Kristian during session 2):
   > "Chet online. PM/EA for Tune Outdoor. OpenAI Codex 5.4 primary, local Gemma 4 for background. Graphiti REST on :8100. Memory stack live. Right configuration?"

2. **Verify operator profile.** Read USER.md. Confirm with Kristian:
   - Timezone (default: TBD — what's Tune Outdoor's local time?)
   - Telegram bot handle (what was actually created? verify)
   - Hard nos list — any additions specific to Tune Outdoor?
   - Team members to register now (each gets a section in USER.md)

3. **Probe OpenClaw health + tool access.**
   ```bash
   openclaw health
   openclaw doctor
   openclaw models auth list
   openclaw channels list
   ```
   Report which tools are green, which need attention.

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
     -d '{"body": "Chet deployed and bootstrapped on Tune Outdoor Mac on 2026-05-08 by Ryan Shuken with Kristian Arnold. Primary: OpenAI Codex 5.4 via OAuth. Background: local gemma4:e4b via Ollama. Structured memory: Graphiti + Neo4j via REST on localhost:8100. Context engine: Lossless Claw. Embeddings: Gemini (gemini-embedding-001).", "name": "chet-deploy-bootstrap"}'
   # Blocks 30-120s; returns {"ok": true, ...}
   ```
   Confirm retrievable: `curl -sS 'http://localhost:8100/api/search?q=Chet+deployed'`

6. **Create the first markdown memory entry.**
   ```
   workspace/memory/2026-05-08.md:
   Chet deployed today on Tune Outdoor's Mac (Apple Silicon, macOS).
   Initial probe results: [from step 3]
   Memory stack health: [from step 4]
   First Graphiti episode: chet-deploy-bootstrap
   Operator: Kristian Arnold (kristian@tuneoutdoor.com)
   Open items: [anything that needed manual intervention]
   ```

7. **Confirm heartbeat cadence.** Read HEARTBEAT.md with Kristian. For each pulse, confirm:
   - Timezone is right for Tune Outdoor
   - The channel it posts to (briefing, alerts) exists / is decided
   - Graphiti REST is reachable for auto-ingest

8. **Verify launchd pulses are registered** (done by `install-flyn.sh`, but confirm):
   ```bash
   launchctl list | awk '/ai\.flyn\.(pulse|gemma4-warm-at-boot)/{print $3}'
   # Expect 6 labels:
   #   ai.flyn.pulse.morning-digest
   #   ai.flyn.pulse.memory-autosave
   #   ai.flyn.pulse.health-check
   #   ai.flyn.pulse.memory-rollup
   #   ai.flyn.pulse.model-drift
   #   ai.flyn.gemma4-warm-at-boot
   ```

   Scripts live at `~/.openclaw/scripts/flyn/*.sh`; logs at `~/.openclaw/logs/cron-<label>.{log,err}`. To re-register or repair: `./deploy/cron/register-flyn-crons.sh` from the repo root.

9. **Smoke-test the curl-from-exec pattern.** Verifies Chet can use structured memory live (not just via setup scripts). Ask Chet:
   > "Record that we just completed bootstrap by posting an episode via the REST API, then confirm with a search."

   Watch Neo4j episode count increment.

10. **Tune Outdoor first-session adds (specific to this deploy):**
    - **Provision Chet's Workspace user** (session 2 §4) — chet@tuneoutdoor.com or similar. Capture the email handle in USER.md.
    - **Pair the primary Telegram bot** with Kristian. Capture the bot handle in IDENTITY.md.
    - **Decide on briefing topic / channel name** — placeholder until Google Chat integration is built (see TOOLS.md "Pending integrations").
    - **Identify the first priority use case** to wire end-to-end (warranty intake / market research / competitor analysis — Kristian's call). Configure it during session 2 §3.

11. **Ask about preferences.** Anything to adjust in:
    - AGENTS.md approval gates?
    - SOUL.md voice (right register for Tune's team culture?)
    - HEARTBEAT.md cadence?
    - Additional hard nos for USER.md?

12. **Mark bootstrap complete.**
    ```bash
    mv ~/.openclaw/workspace/BOOTSTRAP.md ~/.openclaw/workspace/BOOTSTRAP-completed-$(date +%Y-%m-%d).md
    ```
    Chet proposes this rename; operator confirms before it happens.

---

## If bootstrap is interrupted

Leave this file in place. Record progress in `workspace/memory/YYYY-MM-DD.md`. On resume, skip completed items.

## Do NOT put here

- Permanent config — that's `openclaw.json` or template files
- Long-term memory — that's `MEMORY.md` or Graphiti (via `/api/episode`)
- Recurring tasks — that's `HEARTBEAT.md` / `openclaw cron`
