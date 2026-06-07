---
name: message-contact
triggers:
  - "message Beth"
  - "tell Beth"
  - "DM Beth"
  - "message Eric"
  - "tell Eric"
  - "DM Eric"
  - "send <name> a message"
when-not-to-use:
  - Recipient is anyone outside `skills/_reference/contacts/`
  - Ryan is asking ABOUT a contact, not asking to message them
---

# message-contact

When Ryan explicitly says "message <contact>" in-session — that phrasing
IS the approval per AGENTS.md hard-rule 3. Send via their primary channel
using Ryan's text.

## Steps

1. **Identify the contact.** Load `skills/_reference/contacts/<name>.md`
   for their primary channel + chat_id.
2. **Send via their primary channel.** Most contacts use Telegram:
   ```
   curl -sS -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
     -d "chat_id=${CHAT_ID}" \
     --data-urlencode "text=${MESSAGE}"
   ```
   (Plain text. No parse_mode — Markdown breaks on user-content embeds,
   see CHANGELOG fix(pulses): overnight digest.)
3. **Report back.** One sentence to Ryan: "Sent to Beth." with the chat
   message_id for receipt confirmation.

## What counts as authorization

Ryan says: **"message Beth that Q2 is delayed"** → authorized, send it.
Ryan says: **"should I message Beth?"** → NOT authorized, ask first.
Ryan says: **"draft a message to Beth"** → draft only, do NOT send.

## Anti-pattern

Don't paraphrase Ryan's message. Send what he literally said unless he
asks you to polish it.
