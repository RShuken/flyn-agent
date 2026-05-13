# WIKI — OpenLiteracy Mission Control

This is **the project management HQ** for the OpenLiteracy Phase 2 engagement. It is Flyn's primary interface for everything OL: tickets (called "questions"), decisions, owners, sprint state, audit history.

**Ryan and Beth treat the wiki as ground truth.** When they say "the wiki", "update the site", "modify [a question]", "log this decision", "what's blocking sprint 1", they mean THIS system — not Notion, not a Google Doc, not GitHub Pages in the generic sense. Use the API below, not a browser.

---

## Where things live

| Surface | URL | Use |
|---|---|---|
| **Public wiki (browser)** | https://ol-explainer-wiki.pages.dev (PIN: `1080`) | Read-only HTML for humans. Renders questions, Gantt, decisions, principles. Powered by the API below. |
| **API** (read + write) | https://4cs-mac-mini.tailc7d8af.ts.net/api | Authoritative state. Use this from `exec` curl calls. |
| **API (local, faster)** | http://127.0.0.1:8200/api | Same API, same data, 127.0.0.1-bound. **Always prefer this** from Flyn-on-4C. |
| **SQLite** | `~/.openclaw/data/ol-pm.db` | Underlying store. Read with `sqlite3` if API is down. NEVER write directly — go through the API so audit + webhooks fire. |
| **Source repo** | `/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase` (private) | Markdown source of truth for question text, sprint plan, synthesis, RESOLVED.md. Auto-deploys the wiki on push. |
| **launchd service** | `ai.flyn.ol-wiki-backend` | The FastAPI server on :8200. KeepAlive=true. |

---

## API key — where it lives

```bash
API_KEY=$(python3 -c "import json; print(json.load(open('/Users/4c/.openclaw/agents/main/agent/auth-profiles.json'))['profiles']['ol_wiki_api:default']['token'])")
```

That's the **bearer key for write endpoints** (`X-API-Key` header). Read endpoints don't need it.

---

## Endpoints — every one Flyn might use

### Reads (no auth needed)

```bash
# Health
curl -sS http://127.0.0.1:8200/api/health

# Aggregate stats (questions × status × owner × sprint × bucket, decision count)
curl -sS http://127.0.0.1:8200/api/stats | jq

# List questions, with filters
curl -sS "http://127.0.0.1:8200/api/questions?limit=200" | jq
curl -sS "http://127.0.0.1:8200/api/questions?owner=Rebecca%20Patterson&status=open" | jq
curl -sS "http://127.0.0.1:8200/api/questions?section=N" | jq                  # all 7 source-of-truth conflicts
curl -sS "http://127.0.0.1:8200/api/questions?target_sprint=1&status=open" | jq # what's blocking sprint 1
curl -sS "http://127.0.0.1:8200/api/questions?q=exit+ticket" | jq              # free-text search

# One question
curl -sS http://127.0.0.1:8200/api/questions/A.5 | jq

# Decisions log (most recent first)
curl -sS http://127.0.0.1:8200/api/decisions | jq
```

### Writes (require `X-API-Key`)

```bash
# Mark a question answered
curl -sS -X POST http://127.0.0.1:8200/api/questions/A.5/answer \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"answer_text": "Two consecutive 0/5 sentence + 0/10 word list, confirmed by Rebecca.",
       "answered_by": "Rebecca Patterson"}' | jq

# Reassign an owner
curl -sS -X POST http://127.0.0.1:8200/api/questions/I.13/reassign \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"owner": "Greta Phillips Kendall", "reason": "co-browser is a UI/UX call"}' | jq

# Log a decision
curl -sS -X POST http://127.0.0.1:8200/api/decisions \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"decided_by": "Sarah Scott Frank",
       "summary": "4 score buckets confirmed (Green / Y-warmup / Y-stay / Red)",
       "body_md": "...rationale + verbatim quote from email of 2026-05-19...",
       "question_ids": ["N.1"],
       "source_meeting": "2026-05-18_mid-sprint-checkin"}' | jq

# Audit log (auth — useful for "what changed today")
curl -sS http://127.0.0.1:8200/api/audit -H "X-API-Key: $API_KEY" | jq '.[:20]'

# Webhooks (subscription CRUD — auth)
curl -sS http://127.0.0.1:8200/api/webhooks -H "X-API-Key: $API_KEY" | jq
```

### Question schema

Every question row has: `id` (e.g. `A.5`), `section` (A-N+P), `section_title`, `text`, `ask` (optional sub-prompt), `bucket` (ai-does / ai-generates / ai-assists / human-only / bucket-unclear / Conflict), `source` (free-text citation), `owner` (full stakeholder name), `status` (open / pending-answer / answered / deferred), `depends_on` (list of other ids), `target_sprint` (1-3 or null), `answered_at` / `answered_by` / `answer_text` (after answer), `source_doc`, `updated_at`.

### Decision schema

`id` (int), `decided_at`, `decided_by` (free-text), `summary` (one-liner), `body_md` (full rationale, markdown), `question_ids` (list), `source_meeting` (folder slug like `2026-05-11_sprint1-kickoff`).

---

## How to USE this in conversations

### When Ryan or Beth says "what's blocking sprint 1?"

```bash
curl -sS "http://127.0.0.1:8200/api/questions?target_sprint=1&status=open&limit=200" | \
  jq -r '.[] | "- \(.id) (\(.owner)): \(.text[:120])"' | head -15
```

Report the top 10 by owner, group by sprint phase, surface anything answered today.

### When Beth says "update Q A.5 — Rebecca answered it"

1. Get the current question to confirm context: `GET /api/questions/A.5`
2. Ask Beth to paste Rebecca's actual answer (verbatim)
3. `POST /api/questions/A.5/answer` with `answered_by="Rebecca Patterson"` and her exact text
4. Confirm back: "Marked A.5 answered. Webhook fired to your DM. Live at https://ol-explainer-wiki.pages.dev (refresh)."

### When Ryan says "log this decision: X"

1. Ask which question_ids it resolves (if any)
2. Ask for source_meeting slug or `null`
3. `POST /api/decisions` with full body
4. Webhook fires to Beth's DM automatically

### When Beth or Ryan asks "what does the wiki show for Rebecca?"

```bash
curl -sS "http://127.0.0.1:8200/api/questions?owner=Rebecca%20Patterson" | jq
```

### When they say "we need to change the wiki" (text content of a question)

The **wiki text comes from the source repo, not the DB.** Two paths:

- **Status change / answer / decision** → use the API endpoints above (DB-level)
- **Edit question wording / add new question** → edit the markdown at `/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase/docs/02-open-questions/00_master-question-registry_Beth.md`, then re-run the seed: `python3 ~/.openclaw/scripts/flyn/pm/registry_parser.py --project openliteracy --bootstrap` (slow — calls Graphiti) OR via a more targeted SQLite UPDATE if just patching wording. **Default to asking Ryan or Beth** before editing canonical text — that's an approval-gated change.

### When they say "redeploy the wiki"

The wiki auto-deploys via `ai.flyn.ol-wiki-autodeploy` (polls `origin/main` every 3 min, runs `wrangler pages deploy`). To trigger immediately:

```bash
launchctl kickstart gui/$(id -u)/ai.flyn.ol-wiki-autodeploy
```

---

## Hard rules

1. **Never mutate the DB directly via `sqlite3`** — go through the API so audit_log + webhooks fire.
2. **Always confirm answer text verbatim** before posting `/answer` — don't paraphrase Rebecca / Sarah / Greta unless Beth or Ryan explicitly OKs.
3. **`source_meeting` field on decisions** uses the folder slug convention from `docs/00-source/meetings/YYYY-MM-DD_short-name/`. Reuse existing slugs if possible.
4. **For wording changes to canonical question text** (the registry markdown), ALWAYS ask before editing. Status, answers, owner reassignments via API are normal flow; rewriting questions is editorial.
5. **Webhooks fire automatically** on every mutation → the bridge service DMs Beth (decisions, reassignments) and Beth+Ryan (answers). Don't manually send Telegram about every change.

---

## When the API is down

- `curl http://127.0.0.1:8200/api/health` returns nothing → check `launchctl print gui/$(id -u)/ai.flyn.ol-wiki-backend | grep exit`
- If service is stuck, kickstart it: `launchctl kickstart -k gui/$(id -u)/ai.flyn.ol-wiki-backend`
- If that fails, fall back to reading the source repo directly (`/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase/docs/02-open-questions/00_master-question-registry_Beth.md`) — slower but always works.

---

## What this wiki is NOT

- Not Notion. Not a Google Doc. Not Confluence. Not GitHub Pages generically — it's the custom OL master plan at `ol-explainer-wiki.pages.dev`.
- Not where Cora's PM state lives (Cora has its own placeholder config at `~/.openclaw/projects/cora/config.yaml` — registry not yet seeded).
- Not where contracts / billing / payroll lives.

For Cora-related PM operations, **the wiki does not yet apply.** Either say so to Ryan/Beth, or — if Beth asks about Cora PM — surface that as an open gap and ask what system she wants Cora tracked in.
