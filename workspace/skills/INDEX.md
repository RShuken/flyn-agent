# Skills index

The agent reads this table every turn to decide which skill body (if any)
to load. Each row says **when** to use a skill and **where** to find its
body.

**Rules:**
- If the user's message matches a trigger → load the listed `path` body in
  that turn only. Do NOT pre-load.
- If no trigger matches → respond directly. Do NOT call memory tools or
  load skills "just in case."
- If multiple triggers match, pick the most specific. When in doubt, ask
  Ryan which path he wants.
- Skills marked `type: cron` run automatically on schedule — you don't
  invoke them, but you can mention them if relevant.
- Skills marked `type: reference` are lazy-load context files. Load when
  you need detail on a contact, a project, or an operations runbook.

## On-demand skills (agent invokes when triggered)

| Skill | Trigger (when to use) | Path | Notes |
|---|---|---|---|
| memory-recall | User asks "what do we know about X" / "have we discussed Y" / "do we have anything on Z" | skills/memory-recall/SKILL.md | Use `flyn-mem query` + Graphiti. Do NOT use for "X is broken" reports. |
| project-status-update | User reports a milestone / decision / status change on Cora, OpenLiteracy, or a client | skills/project-status-update/SKILL.md | POSTs to memory router + updates project markdown. |
| message-contact | User says "message Beth", "tell Eric X", or "DM <contact> Y" | skills/message-contact/SKILL.md | Authorized by Ryan's in-session phrasing; uses Telegram primary channel. |
| broken-link-fix | User reports a broken / invalid / 404 URL | skills/broken-link-fix/SKILL.md | Check `git log`, suggest the new branch/path, offer to update the source. |
| ship-gate-check | User asks "is X deployed", "what's the gate status of Y", "did Z ship" | skills/ship-gate-check/SKILL.md | Reads the rubric files under `deploy/outcomes/`. |
| commitment-followup | User says "remind me", "follow up on", "ping me about X later" | skills/commitment-followup/SKILL.md | Creates a `commitments` entry via openclaw. |

## Background skills (cron-triggered, no agent invocation)

| Skill | Schedule | Owner | Notes |
|---|---|---|---|
| overnight-digest | 06:30 daily | ai.flyn.pulse.orchestrator-overnight-digest | Sends Ryan a digest of the last 24h. Plain text — see CHANGELOG PR fix(pulses). |
| morning-briefing | 07:00 weekdays | ai.flyn.pulse.morning-digest | Day-of-work briefing. |
| nightly-backup | 02:00 daily | ai.flyn.pulse.nightly-backup | Backs up `~/.openclaw` state. |
| memory-rollup | hourly | ai.flyn.pulse.memory-rollup | Compacts the day's events to graphiti. |
| memory-autosave | every 30 min | ai.flyn.pulse.memory-autosave | Snapshots conversation memory. |
| model-drift | daily | ai.flyn.pulse.model-drift | Audits model-config for stale references. |
| health-check | hourly | ai.flyn.pulse.health-check | Probes :8100/:8200/:8400/:18789. |
| telegram-menu-trim | every 10 min | ai.flyn.pulse.telegram-menu-trim | Keeps the bot's command menu under Telegram's 5700-char budget. |
| meeting-categorize | daily | ai.flyn.pulse.meeting-categorize | Tags Fathom transcripts by project. |
| conv-summarize-backfill | 04:15 daily | ai.flyn.pulse.conv-summarize-backfill | Re-attempts any stuck conv-tier summaries. |
| gemma4-warm | at-boot | ai.flyn.gemma4-warm-at-boot | Pre-loads gemma4 into Ollama so first inference is fast. |

## Reference (lazy-load only when relevant)

| Path | When to load |
|---|---|
| skills/_reference/contacts/ryan.md | Need a detail about Ryan beyond chat_id |
| skills/_reference/contacts/beth.md | Sending a message to Beth, or talking about Cora COO duties |
| skills/_reference/contacts/eric.md | Sending a message to Eric, or talking about Cora CEO duties |
| skills/_reference/projects/cora.md | Discussion about Cora platform / migration / governance |
| skills/_reference/projects/openliteracy.md | OpenLiteracy questions or curriculum work |
| skills/_reference/projects/4c.md | 4C consulting / VC engagement context |
| skills/_reference/operations/tools-catalog.md | Need detail on a tool's edge cases or auth |
| skills/_reference/operations/identity.md | Deep identity questions; "who are you" beyond the system prompt |
| skills/_reference/operations/voice.md | Calibrating tone for a specific channel or audience |
| skills/_reference/operations/heartbeat-schedule.md | Reasoning about cron timing or schedule conflicts |

---

**Maintenance:** when adding a new skill, add a row here AND create
`skills/<name>/SKILL.md` with a YAML frontmatter block:

```yaml
---
name: my-skill
triggers:
  - "trigger phrase 1"
  - "trigger phrase 2"
when-not-to-use:
  - "case where this doesn't apply"
---
```

When deprecating, move the skill body to `skills/_archive/` and delete the
row here.
