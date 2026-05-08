# HEARTBEAT — Chet

Recurring pulses Chet runs without being asked. Run as `openclaw cron add` jobs (or via `register-flyn-crons.sh` from the deploy repo, see BOOTSTRAP), NOT inside a long-lived openclaw session.

> **Pulse-name note:** the launchd labels and script paths inherit `flyn` from the upstream deploy (`ai.flyn.pulse.*`, `~/.openclaw/scripts/flyn/*.sh`). The work each pulse does is Chet's; only the file/label names are flyn-prefixed. Don't rename at runtime.

**Routing rule:** every pulse below uses local models (Ollama / Gemini-embeddings), not frontier cloud. Frontier is reserved for live operator turns.

---

## Pulse: morning-digest

- **When:** weekdays 07:00 Tune Outdoor local time (confirm timezone during BOOTSTRAP)
- **What:** summarize overnight activity — new emails (unread, not auto-reply) on Chet's Workspace mailbox, calendar for today, status of any open warranty/research cases, failed cron runs in last 24h. Post to Chet's primary channel:
  - **Today (interim):** Telegram briefing topic
  - **Future:** Google Chat space `#chet-briefing` (once Chat integration is built)
- **Model:** local (Gemma 4 via Ollama) — no frontier cloud.
- **Success:** formatted message in briefing channel; no errors in `~/.openclaw/logs/heartbeat-YYYY-MM-DD.log`.
- **On failure:** alert to Kristian on Chet's primary channel.

## Pulse: hourly-memory-auto-save

- **When:** top of every hour during waking hours (06:00–23:00 local)
- **What:** TWO writes per fire:
  1. Append a compact prose rollup of the last hour's session activity to `workspace/memory/YYYY-MM-DD.md` (markdown tier).
  2. POST the same rollup to `http://localhost:8100/api/episode` so Graphiti extracts typed facts into Neo4j (structured tier).
- **Why both:** markdown stays human-readable + searchable via sqlite-vec; Graphiti extracts typed entities/edges with `valid_at` for temporal queries. Two writes, one source of truth.
- **Model:** local only (gemma4:e4b runs inside the Graphiti entity-extraction pipeline — the POST blocks while it runs).
- **Success:** one markdown append + one POST returning `{"ok": true}` per fire; no duplicate entries.
- **On failure:** silent if no changes to roll up. If the POST fails twice consecutively, check `curl http://localhost:8100/api/health` and the launchd agent `ai.flyn.graphiti-api`. Do the markdown write regardless — markdown is the fallback tier.

## Pulse: daily-health-check

- **When:** daily 22:00 local
- **What:** `openclaw health && openclaw doctor && openclaw models auth list && df -h ~ | tail -1 | awk '{print $5}'` — verify OpenAI Codex OAuth not expired, disk not above 85%, all core subsystems OK. Output to `~/.openclaw/logs/health-YYYY-MM-DD.log`.
- **Model:** none (bash only).
- **Success:** silent (silent = healthy).
- **On failure:** alert with the specific check that failed.

## Pulse: weekly-memory-rollup

- **When:** Sundays 20:00 local
- **What:** read the last 7 days of `workspace/memory/*.md`, produce a compact weekly rollup at `workspace/memory/weekly/YYYY-WW.md`, then trim daily files older than 30 days to Cold tier.
- **Model:** local (Gemma 4).
- **Success:** one weekly rollup file produced; daily files trimmed.
- **On failure:** alert; do NOT delete daily files if rollup failed.

## Pulse: weekly-model-drift-check

- **When:** Sundays 21:00 local
- **What:** run `openclaw models list --all` + diff against last week's snapshot. Flags if any configured model moved to "Unknown" status.
- **Model:** none (bash + diff).
- **Success:** silent; diff file at `~/.openclaw/logs/model-drift-YYYY-WW.log`.
- **On failure:** alert with which model resolution changed.

---

## Tune-Outdoor-specific pulses (TBD — populate during/after session 2 §3)

After Kristian configures the priority use cases, add pulses for them. Likely candidates:

- **warranty-intake-pull** — every N minutes, scan the warranty inbox for new submissions, route + summarize.
- **competitor-watch** — daily, check competitor websites/social for changes; surface deltas.
- **market-research-digest** — weekly, run a query template and produce a brief.

Don't add these until Kristian has approved each one and named the channel it should report to.

---

## Pulse Discipline

- Heartbeats run via launchd (registered by `deploy/cron/register-flyn-crons.sh`), NOT inside long-lived openclaw sessions.
- Every pulse logs to `~/.openclaw/logs/heartbeat-YYYY-MM-DD.log` with start time, end time, exit status.
- A pulse that runs longer than its interval is a bug — fix the pulse, don't extend the interval.
- Heartbeats never modify external state (email, production systems, public posts) without an approval gate (see AGENTS.md).
- If Chet is under heavy interactive load and a pulse fires, the pulse waits up to 2 min then skips that cycle. Missed cycles log a warning, not an error.
