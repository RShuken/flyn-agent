# HEARTBEAT — Flyn

Recurring pulses Flyn runs without being asked. Run as `openclaw cron add` jobs on 4C, NOT inside a long-lived openclaw session.

Per [`feedback_openclaw_local_background_routing.md`](../../): **every pulse here uses local models (Ollama / oMLX), not frontier cloud.** Frontier is reserved for user-chat turns with Rel.

---

## Pulse: morning-digest

- **When:** weekdays 07:00 America/Denver (adjust to Ryan's timezone)
- **What:** summarize overnight activity — new emails (unread, not auto-reply), calendar for today, Cora/Railway deploy status, any failed cron runs from last 24h. Post to Telegram `#flyn-briefing`.
- **Model:** local (Gemma 4 / Qwen 3.5 8B via oMLX) — no cloud calls.
- **Success:** formatted message in briefing topic; no errors in `~/.openclaw/logs/heartbeat-YYYY-MM-DD.log`.
- **On failure:** Telegram alert to Ryan in `#flyn-alerts`.

## Pulse: hourly-memory-auto-save

- **When:** top of every hour during waking hours (06:00–23:00 local)
- **What:** append a compact rollup of the last hour's session activity to `workspace/memory/YYYY-MM-DD.md`. Pattern per `skills/memory-options/community-patterns.md` heartbeat auto-save.
- **Model:** local only.
- **Success:** one appended entry per fire; no duplicate entries.
- **On failure:** silent if no changes to roll up; alert if write fails twice consecutively.

## Pulse: daily-health-check

- **When:** daily 22:00 local
- **What:** `openclaw health && openclaw doctor && openclaw models auth list && df -h ~ | tail -1 | awk '{print $5}'` — verify Codex OAuth not expired, disk not above 85%, all core subsystems OK. Output to `~/.openclaw/logs/health-YYYY-MM-DD.log`.
- **Model:** none (bash only).
- **Success:** silent (silent = healthy).
- **On failure:** Telegram alert with the specific check that failed.

## Pulse: weekly-memory-rollup

- **When:** Sundays 20:00 local
- **What:** read the last 7 days of `workspace/memory/*.md`, produce a compact weekly rollup at `workspace/memory/weekly/YYYY-WW.md`, then trim daily files older than 30 days to Cold tier. Per `memory-options/community-patterns.md`.
- **Model:** local (Gemma 4).
- **Success:** one weekly rollup file produced; daily files trimmed.
- **On failure:** alert; do NOT delete daily files if rollup failed.

## Pulse: weekly-model-drift-check

- **When:** Sundays 21:00 local
- **What:** run `openclaw models list --all` + diff against last week's snapshot. Flags if any configured model moved to "Unknown" status (per OpenClaw #37623). Per `skills/deploy-model-routing.md` "Platform caveats".
- **Model:** none (bash + diff).
- **Success:** silent; diff file at `~/.openclaw/logs/model-drift-YYYY-WW.log`.
- **On failure:** alert with which model resolution changed.

---

## Pulse Discipline

- Heartbeats run via `openclaw cron add`, NOT inside long-lived openclaw sessions.
- Every pulse logs to `~/.openclaw/logs/heartbeat-YYYY-MM-DD.log` with start time, end time, exit status.
- A pulse that runs longer than its interval is a bug — fix the pulse, don't extend the interval.
- Heartbeats never modify external state (email, production systems, public posts) without an approval gate (see IDENTITY.md).
- If Flyn is under heavy interactive load and a pulse fires, the pulse waits up to 2 min then skips that cycle. Missed cycles log a warning, not an error.
