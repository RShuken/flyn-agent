# BOOTSTRAP — Flyn

Loaded **only on the first session after deploy**. Rename to `BOOTSTRAP-completed-YYYY-MM-DD.md` after the checklist is done so it doesn't load again.

---

## First-session checklist

When Flyn boots for the first time on 4C:

1. **Confirm identity.** Read IDENTITY.md + SOUL.md. Introduce self to Ryan in character: "Flyn online. CEO of 4C. Codex 5.4 primary, local Gemma 4 for background. OAC peering available." Ask: "Right configuration?"

2. **Verify operator profile.** Read USER.md. Confirm:
   - Timezone (default: America/Denver — correct?)
   - Telegram bot handle (`@FlynBot` or similar — what was actually created?)
   - Hard nos list — any additions for this deployment?

3. **Probe tool access.** Run and report results:
   ```bash
   openclaw health
   openclaw doctor
   openclaw models auth list
   openclaw models list --all | head -30
   openclaw skills list | head -20
   openclaw memory status --json
   openclaw channels list
   ```
   Report which tools work, which need auth / re-auth, which are missing.

4. **Verify memory stack is installed.** Per `deploy/install-memory-stack.md`:
   - Lossless Claw configured in `openclaw.json` `contextEngine` slot? (`openclaw config get contextEngine`)
   - `gemini-embedding-2-preview` set as embedding provider? (`openclaw memory status --json | jq '.embedding'`)
   - Gemma 4 pulled into Ollama/oMLX? (`ollama list | grep gemma4` or `omlx ls`)
   - mem0 installed? (`openclaw skills list | grep mem0`)
   - EmbeddingGemma available as local fallback? (`ollama list | grep embeddinggemma`)

5. **Create the first memory entry.**
   ```
   workspace/memory/YYYY-MM-DD.md:
   Flyn deployed today on Mac Mini 4C.
   Initial probe results: [from step 3]
   Memory stack: [from step 4]
   Open items: [anything that needed manual intervention]
   ```

6. **Confirm heartbeats.** Read HEARTBEAT.md with Ryan. For each pulse, confirm:
   - Timezone is right
   - The channel it posts to exists (`#flyn-briefing`, `#flyn-alerts`)
   - The local model it needs is pulled

7. **Register cron jobs.** After Ryan approves the heartbeat cadence:
   ```bash
   openclaw cron add --name morning-digest --cron "0 7 * * 1-5" --command "~/.openclaw/scripts/morning-digest.sh"
   openclaw cron add --name hourly-memory-save --cron "0 6-23 * * *" --command "~/.openclaw/scripts/memory-autosave.sh"
   openclaw cron add --name daily-health-check --cron "0 22 * * *" --command "~/.openclaw/scripts/health-check.sh"
   openclaw cron add --name weekly-memory-rollup --cron "0 20 * * 0" --command "~/.openclaw/scripts/memory-rollup.sh"
   openclaw cron add --name weekly-model-drift --cron "0 21 * * 0" --command "~/.openclaw/scripts/model-drift.sh"
   openclaw cron list
   ```

8. **Test OAC peering with another agent.**
   - Flyn sends a peer handshake over OAC gateway: "Flyn online. Open for peer traffic."
   - Confirm the peer (e.g., Rel) acknowledges. Round-trip verified.
   - Test one peer collaboration: Flyn asks the peer for something it specializes in (or vice versa) → confirm OAC routing works in both directions, neither side acting as principal.

9. **Ask about preferences.** Anything to adjust in:
   - AGENTS.md approval gates?
   - SOUL.md voice (too dry? not dry enough?)
   - HEARTBEAT.md cadence?
   - Additional hard nos for USER.md?

10. **Mark bootstrap complete.** Once all above is confirmed:
    ```bash
    mv ~/.openclaw/workspace/BOOTSTRAP.md ~/.openclaw/workspace/BOOTSTRAP-completed-$(date +%Y-%m-%d).md
    ```
    Flyn proposes this rename; Ryan confirms before it happens.

---

## If bootstrap is interrupted

If the first session ends before all checklist items are done, leave this file in place. Record progress in `workspace/memory/YYYY-MM-DD.md` so the next session knows which items are still pending. On resume, skip completed items.

## Do not put here

- Permanent config — that's `openclaw.json` or template files
- Long-term memory — that's `MEMORY.md`
- Recurring tasks — that's `HEARTBEAT.md` / `openclaw cron`
