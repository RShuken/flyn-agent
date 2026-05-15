# Krisp.ai Webhook → Flyn Meeting Inbox → Nightly Categorizer

**Date:** 2026-05-14
**Owner:** Ryan Shuken (operator), Flyn (implementer)
**Status:** Approved architecture; ready for implementation plan

## Goal

Receive Krisp.ai meeting transcripts via webhook, park them in a Flyn-wide
inbox, and let a nightly cron categorize each meeting and route it to the
right project (OpenLiteracy today, Cora later, personal/archive fallback).
Anything the categorizer can't confidently classify surfaces in the
morning Telegram digest with a one-tap `/route` command.

## Non-goals

- No transcription. Krisp delivers ready text.
- No real-time routing. Categorization is nightly.
- No new public service. Reuse the existing FastAPI on port 8200 +
  Tailscale Funnel.
- No Krisp HMAC signature verification (Krisp does not sign requests;
  we authenticate via a shared-secret header we configure on Krisp's
  side).

## Architecture

```
                          shared-secret header
                          ┌──────────────────────┐
   Krisp (cloud) ────────►│ POST /api/meetings/  │
                          │   krisp              │  (existing FastAPI:8200,
                          │  (validate + persist │   public via Tailscale Funnel)
                          │   raw payload)       │
                          └──────────┬───────────┘
                                     │
                                     ▼
                  ~/.openclaw/data/flyn-meetings.db
                  ┌────────────────────────────────┐
                  │ meeting_events (raw payloads)  │  ← idempotent, append-only
                  │ meetings (extracted + state)   │  ← one row per Meeting ID
                  └──────────┬─────────────────────┘
                             │
                             │  read status='pending'
                             ▼
              ┌─────────────────────────────────┐
              │ flyn-meeting-categorizer        │  ← new launchd cron @ 02:30
              │ (rules → claude -p fallback)    │     idempotent, run nightly
              └─────────┬───────────────┬───────┘
                        │               │
              routed (status='routed')  unclassified
                        ↓               (status='review')
                  1. write transcript          │
                     into project repo         │
                  2. graphiti_episode()        │
                  3. telegram_send()           │
                                               ▼
                                 morning_standup.py emits
                                 "Unclassified meetings"
                                 section w/ `/route` hints
                                               │
                                               ▼
                                 openclaw Telegram bot
                                 parses `/route <id> <project>` →
                                 updates DB → re-runs router
```

## Subsystem 1 — Krisp webhook receiver

### Endpoint

```
POST /api/meetings/krisp
Headers:
  Content-Type: application/json
  X-OL-Krisp-Token: <shared secret>     ← we configure in Krisp UI
Body: { whatever Krisp sends }
```

### Behavior

1. Validate `X-OL-Krisp-Token` against env `FLYN_KRISP_TOKEN`. Missing or
   mismatched → `401`. Constant-time compare via `hmac.compare_digest`.
2. Read raw JSON body (raw bytes, then `json.loads`). On parse failure →
   `400`.
3. Compute `event_id`: prefer payload's `event_id` / `id` / `uuid`. Fallback:
   `sha256(raw_body)[:16]`.
4. Insert into `meeting_events` (idempotent via UNIQUE constraint on
   `event_id`; duplicate POST → `200 { "received": true, "duplicate": true }`).
5. Best-effort extract meeting metadata: `meeting_id`, `title`, `started_at`,
   `ended_at`, `duration_seconds`, `attendees[]`, `transcript_text`,
   `notes_text`, `outline_text`, `key_points_text`, `meeting_url`. Defensive
   `.get()` with `None` defaults — we *do not know exact field names* until
   the first real payload, so this parser must tolerate missing fields and
   we log what we couldn't map.
6. UPSERT into `meetings` keyed by `meeting_id`. If the meeting row exists,
   merge the new event's content into the existing row (transcript event
   fills `transcript_text`; notes event fills `notes_text`; etc.).
7. Set `status='pending'` on new rows; leave existing status alone (so a
   "notes" event arriving after a meeting is already routed doesn't reset
   it).
8. Append to `audit_log` (existing pattern).
9. Return `200 { "received": true, "event_id": "..." }`. Krisp expects 2xx;
   anything else triggers retries on their side.

### Rate limit

`limiter.limit("30/minute")` — Krisp won't approach this; protects against
secret-leak misuse.

### Response time budget

≤ 250 ms p95. All work happens synchronously; no background threads needed
because we're not making outbound calls in the handler.

## Subsystem 2 — Meeting inbox (flyn-meetings.db)

New SQLite file at `~/.openclaw/data/flyn-meetings.db`. Lives next to
`ol-pm.db` but is logically separate (Flyn-wide, not OL-specific).

### Schema

```sql
CREATE TABLE meeting_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT    NOT NULL UNIQUE,
    received_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    source       TEXT    NOT NULL DEFAULT 'krisp',
    event_type   TEXT,                              -- transcript/notes/outline/key_points
    meeting_id   TEXT,                              -- Krisp meeting id (may be null on parse fail)
    raw_payload  TEXT    NOT NULL                   -- full original JSON
);
CREATE INDEX idx_events_meeting ON meeting_events(meeting_id);

CREATE TABLE meetings (
    meeting_id        TEXT PRIMARY KEY,              -- Krisp meeting id
    title             TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    duration_seconds  INTEGER,
    meeting_url       TEXT,
    attendees         TEXT    NOT NULL DEFAULT '[]', -- JSON array of {name?, email?}
    transcript_text   TEXT,
    notes_text        TEXT,
    outline_text      TEXT,
    key_points_text   TEXT,
    status            TEXT    NOT NULL DEFAULT 'pending',
                       -- pending | classifying | routed | review | dropped | error
    routed_project    TEXT,                          -- e.g. 'openliteracy'
    routed_commit_sha TEXT,                          -- where transcript landed
    classifier_reason TEXT,                          -- why we picked this project
    classifier_confidence TEXT,                      -- 'rule' | 'llm-high' | 'llm-low'
    first_seen_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    routed_at         TEXT
);
CREATE INDEX idx_meetings_status ON meetings(status);
CREATE INDEX idx_meetings_started ON meetings(started_at DESC);

CREATE TABLE meeting_audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL DEFAULT (datetime('now')),
    meeting_id TEXT,
    actor   TEXT    NOT NULL,                       -- 'krisp-webhook' | 'categorizer' | 'route-cmd:ryan'
    action  TEXT    NOT NULL,
    payload TEXT    NOT NULL DEFAULT '{}'
);
```

### Module: `wiki-backend/meetings_db.py`

Mirrors `db.py`'s pattern: `_connect()`, `init_db()` (called from app
lifespan), `get_conn()` FastAPI dependency, schema migrations idempotent.
Separate connection pool so the OL wiki and meeting routes don't share
locks unnecessarily.

## Subsystem 3 — Nightly categorizer

### Module: `pm/meeting_categorizer.py`

Run by a new launchd cron at 02:30 daily.

```
main():
  for m in db.query("SELECT * FROM meetings WHERE status='pending'"):
    mark m.status = 'classifying'
    project, confidence, reason = classify(m)
    if project and confidence == 'rule':
        route_meeting_to_project(m, project)
        mark routed
    elif project and confidence == 'llm-high':
        route_meeting_to_project(m, project)
        mark routed
    else:
        mark m.status = 'review', store reason
```

### Classifier logic

```
classify(meeting) -> (project|None, confidence, reason):
  # Layer 1: rules from each project's config.yaml
  for project_slug in list_projects():
      cfg = load_project(project_slug)
      hit = match_attendees(meeting.attendees, cfg.stakeholders)
      if hit:
          return project_slug, 'rule', f"attendee {hit.email} matches {project_slug}"
      hit = match_title(meeting.title, cfg.meeting_keywords)
      if hit:
          return project_slug, 'rule', f"title contains '{hit}'"

  # Layer 2: claude -p fallback (only if rules didn't fire)
  if has_claude_p():
      prompt = build_classifier_prompt(meeting, list_projects_with_descriptions())
      result = run_claude_p(prompt, timeout=60)
      parsed = parse_classifier_output(result)
      if parsed and parsed.confidence >= 0.8:
          return parsed.project, 'llm-high', parsed.reason
      if parsed and parsed.confidence < 0.8:
          return parsed.project, 'llm-low', parsed.reason

  return None, 'unknown', 'no rule + no llm decision'
```

`claude -p` invocation:

```
claude -p "$(cat prompt)" --output-format json
```

Prompt template (`pm/prompts/meeting_classifier.md`):

```
You are Flyn's meeting categorizer. Given a meeting and a list of
projects, return JSON:

{"project": "<slug>" | null, "confidence": 0.0-1.0, "reason": "..."}

Be conservative. If the meeting could plausibly belong to any project
including personal/social, return null with low confidence.

Projects:
- openliteracy: ...one-line description from cfg.display_name...
  Stakeholders: Sarah Scott Frank, Rebecca Patterson, Greta Phillips Kendall, Eric Schneider, Beth Kukla
- cora: ...
  Stakeholders: ...

Meeting:
- Title: {title}
- Attendees: {attendees}
- Started: {started_at}
- Notes excerpt: {notes_text[:2000]}
```

### Routing — `_lib.route_meeting_to_project()`

Extracted from `fathom_router.py`'s `route_transcript()` pattern:

1. `git_pull(project.repo_path)`
2. Build target path:
   `docs/00-source/meetings/<date>_<slug>/transcript.md`
3. Write the transcript (with header block: source=krisp, attendees, etc.).
4. Optionally write `notes.md`, `outline.md`, `key_points.md` if present.
5. Append `WORKLOG.md` entry.
6. `git_commit_and_push()` with message
   `docs(meetings): add Krisp transcript for <date> <slug>`.
7. `graphiti_episode()` with attendees + decisions tagged.
8. `telegram_send()` to operators (per project's
   `cadence.morning_standup.recipients`).
9. Update DB: `status='routed'`, `routed_project`, `routed_commit_sha`,
   `routed_at`.

The `slug` from meeting title: `slugify(title)[:40]`. Collisions get a
numeric suffix.

### Cron registration

New shell script `deploy/cron/scripts/meeting-categorize.sh`. Plist
`ai.flyn.pulse.meeting-categorize.plist` at 02:30 daily. Output to
`~/Library/Logs/ai.flyn.pulse.meeting-categorize.{out,err}.log`.

## Subsystem 4 — Morning digest extension + /route command

### Morning standup (`pm/morning_standup.py`)

Add a new section after existing sections:

```
🎤 Unclassified meetings (N)
  1. Mon 3pm — "Sync w/ Jen" (28 min, 2 attendees)
     /route 1 openliteracy
     /route 1 cora
     /route 1 skip
  2. ...
```

Section is omitted if zero rows in `status='review'`.

### Telegram `/route` command

Openclaw gateway already routes `/`-prefixed messages from Ryan's chat_id
to a per-agent command handler. We add a new handler at
`~/.openclaw/agents/main/commands/route.sh` (or the Python equivalent if
that's the existing convention — verify during implementation).

Command:
```
/route <list-index> <project-slug | skip>
```

The handler:

1. Loads today's review list (cached in the morning digest run; persisted
   as JSON to `~/.openclaw/state/last-review-list.json`).
2. Resolves `<list-index>` → `meeting_id`.
3. If `<project-slug>` is `skip`:
   - Update meeting `status='dropped'`, audit-log the actor.
4. Else if project exists:
   - Call `route_meeting_to_project(meeting, project_slug)`.
   - Audit-log.
5. Reply in Telegram with success/failure.

Unknown index or bad project → friendly error message with usage hint.

## Configuration

### Project config additions

Each project's `~/.openclaw/projects/<slug>/config.yaml` gains a new
optional section:

```yaml
meeting_keywords:
  - "OpenLiteracy"
  - "OL Sprint"
  - "Pearl Platform"
```

(Falls back to existing `fathom.filter_title_substrings` if present, so
existing OL config keeps working without edits.)

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `FLYN_KRISP_TOKEN` | Shared secret. Set in `~/.openclaw/openclaw.json` + injected into the launchd plist for the wiki-backend service. | (no default; webhook returns 503 if unset) |
| `FLYN_MEETINGS_DB` | Override DB path. | `~/.openclaw/data/flyn-meetings.db` |
| `FLYN_CLAUDE_P_BIN` | Path to `claude` binary. | `claude` on PATH |

## Error handling

| Failure | Behavior |
|---|---|
| Krisp posts malformed JSON | 400, no DB write, audit log of the failure with raw body truncated to 1KB |
| Webhook secret missing/wrong | 401, no audit log (avoid log poisoning), counter increments in a separate `webhook_auth_failures` table for monitoring |
| `meeting_events` UNIQUE violation | Treat as duplicate, return 200 with `"duplicate": true` |
| Categorizer fails mid-loop | The current row's `status='classifying'` becomes a stuck state. Cron startup scans for rows stuck in `classifying` >1h old → revert to `pending`, audit-log |
| `git_commit_and_push` fails | Mark `status='error'`, surface in morning digest's "errored meetings" section, do not retry automatically |
| `graphiti_episode` fails | Routing still counts as success (transcript is in the repo); log failure, continue |
| `telegram_send` fails | Log + continue |
| `claude -p` times out (>60s) | Treat as "no LLM decision", fall through to review queue |

## Idempotency contract

- Webhook is idempotent by `event_id` (UNIQUE).
- Routing is idempotent: re-running `route_meeting_to_project` for an
  already-routed meeting is a no-op (check `routed_commit_sha` first).
- Categorizer is safe to re-run: only acts on `status='pending'`.
- Manual `/route` re-runs require explicit `--force` flag (out of scope
  for v1).

## Testing strategy

### Unit tests (`tests/test_krisp_webhook.py`)

1. Valid POST → 200 + DB row + audit entry.
2. Missing token → 401.
3. Wrong token → 401 + constant-time compare proof (use `hmac.compare_digest`).
4. Duplicate `event_id` → 200 `duplicate=true`, no second DB row.
5. Malformed JSON → 400.
6. Multiple events for same `meeting_id` merge into one `meetings` row.
7. Token from env is read at request time, not import time (allows tests
   to override).

### Unit tests (`tests/test_meeting_categorizer.py`)

1. Attendee match → routes to project.
2. Title match → routes to project.
3. No match + no `claude -p` available → status='review'.
4. Mocked `claude -p` high confidence → routes.
5. Mocked `claude -p` low confidence → status='review'.
6. `claude -p` timeout → status='review'.
7. `claude -p` returns non-JSON → status='review', audit-logged.
8. Routing failure (`git_commit_and_push` raises) → status='error'.
9. Re-running categorizer on already-routed meeting is a no-op.
10. Stuck `classifying` rows >1h get reverted to `pending`.

### Integration smoke

`scripts/dev/krisp_smoke.sh`:
```
curl -X POST http://127.0.0.1:8200/api/meetings/krisp \
  -H "X-OL-Krisp-Token: $FLYN_KRISP_TOKEN" \
  -H "Content-Type: application/json" \
  --data @fixtures/krisp_sample.json
```

Then verify DB row, then run categorizer with `--dry-run` flag.

### First-real-payload protocol

For the first week, every payload is captured to `meeting_events.raw_payload`.
A throwaway script `scripts/dev/inspect_payloads.py` pretty-prints them so
we can fix field-mapping bugs against real shapes before turning on
auto-routing. Categorizer accepts a `--noop` flag for this period.

## Deployment plan (rough; implementation plan will refine)

1. Schema migration runs automatically on next wiki-backend restart.
2. Generate `FLYN_KRISP_TOKEN` (32-byte random). Add to
   `~/.openclaw/openclaw.json` under `krisp.webhookToken`. Update the
   wiki-backend plist to export it.
3. `launchctl unload && launchctl load` the wiki-backend plist.
4. Configure Krisp Settings → Integrations → Webhook:
   - URL: `https://4cs-mac-mini.tailc7d8af.ts.net/api/meetings/krisp`
   - Header: `X-OL-Krisp-Token: <token>`
   - Events: all 5
5. Trigger a test meeting from Krisp ("Send Test Event" if available; else
   record a 60-second meeting).
6. `inspect_payloads.py` → adjust field mapping if needed.
7. Enable categorizer cron after 1-2 real meetings have landed cleanly.
8. Document in DISASTER-RECOVERY.md.

## Open items deferred

- Cora project config (`~/.openclaw/projects/cora/config.yaml` exists but
  needs stakeholders + repo + keywords filled in). Categorizer will skip
  routes-to-cora until it does.
- Eventual UI for re-routing already-routed meetings (current design only
  supports first-time routing).
- Krisp signature verification, if/when Krisp adds it.

## Spec self-review checklist

- [x] No TBD/placeholder markers.
- [x] Architecture diagram matches subsystem descriptions.
- [x] All data fields named and typed.
- [x] All HTTP paths and status codes explicit.
- [x] Failure modes enumerated with concrete behaviors.
- [x] Idempotency contract stated.
- [x] Test list exists and covers happy/sad paths.
- [x] Deployment steps are concrete commands or close to it.
- [x] Open items separated from in-scope work.
