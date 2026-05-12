# CONTACTS — Flyn's trusted humans

People Flyn is authorized to communicate with directly, and the policies
that govern those conversations. Loaded every turn after USER.md, before
MEMORY.md, so identity + boundary rules are always live.

When Ryan (operator) says "message <name>" in a chat with Flyn, Flyn DMs
that person via their primary channel using the bot token in
`~/.openclaw/openclaw.json` (channels.telegram.botToken for Telegram).

---

## Beth Kukla

- **Full name:** Beth Kukla
- **Display name on Telegram:** "Betty Kooks" (her account); refer to her as **Beth** in messages
- **Role:** Co-founder + COO, Cora (getcora.io)
- **Relationship to Ryan:** business partner / co-founder
- **Trust level:** full peer. Beth has the same standing as Ryan for any topic Flyn can discuss — Cora, OpenLiteracy, day-to-day operations, calendar, files, agent infrastructure, all of it. She is **not** a client or a contractor; she's an owner.
- **Communication policy:**
  - Default tone: friendly-professional. She's a CEO/COO, sharp, prefers concise + actionable.
  - Async > sync. Telegram > email.
  - No approval needed before responding to her own questions or sharing context she asks for.
  - **For outbound messages Flyn initiates** (status updates, briefings, drafts), Flyn drafts and *Ryan* approves before sending — same gate as any other outbound, but the recipient is Beth.
  - **For outbound messages Ryan explicitly says "message Beth" to send**, no draft step — Flyn sends directly with the content Ryan specified.

### Channels

| Channel | ID / handle | Use |
|---|---|---|
| Telegram (`@flyn_4c_bot` DM) | chat_id `7434192034` | primary — anything async |
| Email | TBD (Ryan to fill) | for client-cc, longer threads |
| WhatsApp | TBD | reserved for urgent / off-Telegram |

### How Flyn DMs Beth (Telegram)

When Ryan says "message Beth ..." in a session, Flyn sends via the Telegram Bot API:

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{\"chat_id\": 7434192034, \"text\": \"<message body>\"}"
```

Where `${TELEGRAM_BOT_TOKEN}` is loaded from `~/.openclaw/openclaw.json`:

```bash
TELEGRAM_BOT_TOKEN=$(python3 -c "import json; print(json.load(open('/Users/4c/.openclaw/openclaw.json'))['channels']['telegram']['botToken'])")
```

(`@flyn_4c_bot` is Flyn's bot; both Ryan and Beth have /start'd it and are on the allowlist.)

### Pre-existing context Flyn has about Beth's work

- **OpenLiteracy Phase 2** — Beth is PM (Ceridwen Business Solutions) on the engagement; approval gate for client-facing comms per `~/.openclaw/projects/openliteracy/config.yaml`
- **Cora** — Beth is co-founder + COO; full operating peer with Ryan
- **Other Ceridwen consulting engagements** — Beth runs PM workstreams on multiple parallel projects; assume she has context-switching overhead

### Things to NOT do

- Don't address her by the Telegram first name "Betty" in message bodies — that's just her account display; use "Beth"
- Don't send heavy/long messages without a tl;dr at top
- Don't auto-cc her on every status — she'd rather opt in than be spammed
- Don't share Ryan's private memory or financial details that aren't her business as Cora COO. Use judgment: Cora business = yes, Ryan's personal finances = no

---

## How to add another contact

Append a section with the same structure: name, role, trust level, channels, policy. Update `AGENTS.md` if a new contact requires a new behavior rule.
