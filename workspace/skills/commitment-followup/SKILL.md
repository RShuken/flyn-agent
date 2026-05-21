---
name: commitment-followup
triggers:
  - "remind me"
  - "follow up on"
  - "ping me about"
  - "ping me when"
  - "in <time>, remind me"
when-not-to-use:
  - Ryan is asking ABOUT a commitment (use openclaw commitments list directly)
---

# commitment-followup

When Ryan asks to be reminded or pinged about something later.

## Steps

1. **Create the commitment via openclaw:**
   ```
   openclaw commitments add \
     --description "<what to remind Ryan of>" \
     --due "<ISO8601 when>" \
     --channel telegram
   ```

2. **If the time is vague** ("later", "tomorrow", "next week"), make a
   reasonable concrete choice and tell Ryan so he can correct it:
   "Set for tomorrow 09:00 — say if that's wrong."

3. **Confirm.** One sentence. Don't dump the full commitment object.

## Edge cases

- If `openclaw commitments add` isn't available (older openclaw), fall
  back to creating a one-shot launchd plist or writing to a `commitments`
  table in memory-router.
- If Ryan wants the reminder via a non-Telegram channel, use that channel's
  primary contact path.
