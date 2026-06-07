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

## Eric Schneider

- **Full name:** Eric Schneider
- **Role:** Tech lead, Futureproof Studio (FPS). Lead dev on OpenLiteracy + co-builder on Cora.
- **Relationship to Ryan:** business partner / dev colleague on multiple engagements.
- **Trust level:** **full peer.** Same standing as Ryan + Beth for any technical or project topic Flyn can discuss.
- **Communication policy:**
  - Default tone: direct + technical. Eric prefers concrete file paths, line numbers, evidence over prose.
  - Async > sync. Telegram first when available.
  - **For outbound messages Flyn initiates** to Eric: same rule as Beth — drafts, then Ryan approves before sending.
  - **For "message Eric" from Ryan**: send directly with Ryan's specified body, no draft step.

### Channels

| Channel | ID / handle | Use |
|---|---|---|
| Telegram | **chat_id TBD** — Eric must /start `@flyn_4c_bot` first; then capture his chat_id via `getUpdates` and update this row + per-project config | primary |
| Email | TBD | longer threads, client-cc |

### Pre-existing context Flyn has about Eric's work

- **OpenLiteracy Phase 2:** tech lead. Owns logic-model synthesis, AI/human capability matrix, sprint-1 master plan structure. Co-author of CLAUDE.md governance for the OL repo.
- **Cora:** co-builder. Owns Quora-flavor persona-driven adaptation engine.
- **`flyn-agent` and `openclaw-base`:** consumer (not maintainer) of Flyn's deploy patterns; runs his own Claude Code workflows.
- **Style:** "Build the Ferrari, not the Fiat" — values up-front planning over rushing to code. Has explicit feedback rules in the OL repo (`feedback_local_then_dev_before_live`, etc.).

### Things to NOT do

- Don't send Eric a long status if a one-liner works. He skims.
- Don't summarize what Ryan or Beth already told him. Cite source quotes if it matters.
- Don't propose architectural changes to OL or Cora without first listing the source-of-truth files Eric would need to review.

---

## Email allowlist (Flyn EmailChannelAdapter)

These email addresses bypass SPF/DKIM verification for inbound mail to Flyn.
The `EmailChannelAdapter` (per Phase 6.5 spec) parses this section at startup
and uses it as the trusted-sender set — any sender NOT in this list AND failing
SPF/DKIM is rejected at `ingest()` (returns None; the message is silently dropped).

**Format:** one email per bullet. Loader at
`flyn_orchestrator/adapters/channels/email_allowlist.py` tolerates whitespace
and ignores HTML comments. TBD/placeholder lines without an `@` are skipped.

- ryanshuken@gmail.com
- beth@cora.community
- eric@cora.community

<!--
Beth's and Eric's @cora.community addresses are placeholders pending DNS
provisioning for the cora.community domain. Update when their real addresses
are live. ryanshuken@gmail.com is the current production source-of-truth.
-->

### How to update

1. Add or remove a bullet under `## Email allowlist (Flyn EmailChannelAdapter)`
2. Restart the orchestrator service: `launchctl unload ~/Library/LaunchAgents/ai.flyn.orchestrator.plist && launchctl load ~/Library/LaunchAgents/ai.flyn.orchestrator.plist`
3. Verify with a sanity-curl: an email from the new sender should produce an `InboundTaskRequest`; one from an excluded sender should drop silently.

The allowlist is checked at `ingest()` time. Pre-existing tasks created by since-removed senders are NOT retroactively rejected.
