---
name: project-status-update
triggers:
  - "update Cora"
  - "Cora status"
  - "OpenLiteracy status"
  - "client update"
  - "we decided"
  - "we shipped"
  - "milestone"
when-not-to-use:
  - Ryan is reporting a bug (use broken-link-fix or respond directly)
  - Ryan is asking ABOUT status (use ship-gate-check or memory-recall)
---

# project-status-update

When Ryan reports a status change, decision, or milestone on a project.

## Steps

1. **Capture the update as a memory event:**
   ```
   curl -sS -X POST http://localhost:8400/api/memory/ingest \
     -H 'Content-Type: application/json' \
     -d '{
       "source": "telegram",
       "event_type": "project_status",
       "subject": "<project>-<short-tag>",
       "body": "<concise prose of what changed>",
       "importance": "warm"
     }'
   ```

2. **For OpenLiteracy:** also update the wiki if Ryan implied a question
   answer or decision (see `skills/_reference/projects/openliteracy-wiki.md`
   for the API).

3. **Confirm back.** One short sentence: "Logged. <subject>" — don't
   re-state what Ryan just said.

## What counts as a status update

- "We shipped X" → log it
- "Decision: we're going with Y" → log it as a decision
- "Beth approved Z" → log it
- "I'm thinking about X" → DON'T log; that's thinking out loud
