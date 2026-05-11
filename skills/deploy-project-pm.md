---
name: "Deploy Project PM"
category: "agent-capability"
subcategory: "project-management"
third_party_service: "github + fathom + telegram + graphiti"
auth_type: "multi"
openclaw_version_required: "2026.4.15+"
version_last_verified: "2026-05-11"
last_checked: "2026-05-11"
maintenance_status: "active"
model_env_var: "PM_DRAFT_MODEL"
clawhub_alternative: "none"
cost_tier: "subscription-only"
privacy_tier: "client-data-mixed"
requires:
  - "GitHub repo access (clone path on the host)"
  - "Fathom MCP server configured (or Fathom API key for cron polling)"
  - "Telegram channel configured with operator + approval topic"
  - "Graphiti REST @ localhost:8100 running (deployed by openclaw-base)"
docs_urls:
  - "https://developers.fathom.ai/mcp-docs/claude"
  - "https://help.graphiti.dev/"
---

# Deploy Project PM

## Compatibility

- **OpenClaw Version**: 2026.4.15+ (uses native `openclaw cron`, `openclaw channels`, `openclaw memory`, and the exec shell tool for curl-to-Graphiti)
- **Status**: WORKING — first deployed 2026-05-11 (OpenLiteracy)
- **Depends on**: `deploy-fathom-pipeline.md` (provides Fathom polling base) + `deploy-knowledge-base.md` (optional, for richer RAG over project docs)
- **Model**: agent default. Override drafting LLM via env var **`PM_DRAFT_MODEL`** when client-comms tone needs a specific voice (e.g. Beth's preferred GPT-4 over Codex).
- **Multi-project by design**: one install supports N projects, each with its own config under `~/.openclaw/projects/<slug>/`. Flyn reads the project roster at boot via `workspace/PROJECTS.md`.

## Purpose

Turn Flyn into a **proactive project manager** for client engagements. Not a chat-only assistant — an agent that:

1. **Tracks the open-questions registry** for each project (currently Beth's `_Beth.md` markdown registries in GitHub) and mirrors questions into Graphiti as typed `(Question)-[OWNED_BY]->(Stakeholder)` / `[DEPENDS_ON]` / `[STATUS]` facts.
2. **Posts a morning standup at 8am local** to the operator's Telegram with critical-path summary: questions blocking sprint exit, stakeholder deadlines (Rebecca-by-5/29 style), new transcripts overnight, and draft follow-ups awaiting approval.
3. **Auto-ingests new Fathom transcripts** into the project's GitHub repo following the repo's meeting-folder convention, commits + pushes, then re-syncs Graphiti.
4. **Drafts client communications** (chase emails to Sarah/Rebecca/Greta, status replies, question batches) and pings the operator on Telegram with approve/edit/skip. Never sends without approval at launch.
5. **Maintains source-of-truth in the GitHub repo**, not in Flyn's own memory. Flyn reads + writes through `git` so the team's other Claude instances see the same state.

**When to use:** Any client engagement where (a) the team has a planning-heavy phase before code, (b) the client is slow to respond and needs structured chasing, (c) multiple sources of truth need reconciliation (e.g. Beth's vs Eric's parallel registries), (d) Fathom is the meeting recorder.

**What this skill installs:**
1. `workspace/PROJECTS.md` — agent-side rules for PM behavior across projects (added to AGENTS.md boot sequence)
2. `~/.openclaw/projects/<slug>/config.yaml` — per-project config (repo path, channels, stakeholders, deadlines, registry pointer)
3. `~/.openclaw/scripts/flyn/pm/` — Python helpers: `registry_parser.py`, `morning_standup.py`, `fathom_router.py`, `comms_drafter.py`
4. Four `openclaw cron` entries: `pm-morning-standup` (daily 8am), `pm-fathom-poll` (hourly), `pm-question-staleness` (every 4h), `pm-deadline-watch` (daily 6am)
5. A Telegram topic `pm-approvals` for client-comms drafts awaiting operator approval

## How Commands Are Sent

Standard protocol — see `_authoring/_deploy-common.md`. `Remote:` runs on 4C; `Operator:` runs locally.

## Variables

Resolve these before executing.

| Variable | Source | Example |
|----------|--------|---------|
| `${PROJECT_SLUG}` | Per-project (kebab-case) | `openliteracy` |
| `${PROJECT_REPO}` | Local clone path of the planning repo | `/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase` |
| `${REGISTRY_PATH}` | Path within the repo to canonical question registry | `docs/02-open-questions/00_master-question-registry_Beth.md` |
| `${OPERATOR_TG_CHAT}` | Telegram chat ID for the PM operator (Beth's DM, or a project group) | `-100xxxxxxxxxx` |
| `${APPROVAL_TG_TOPIC}` | Topic ID in the operator's chat for drafts awaiting approval | `42` |
| `${FATHOM_FILTER}` | Substring or attendee email that flags a meeting as project-relevant | `sarah.scott.frank@openliteracy.com` OR title contains "OL" |
| `${DEADLINE_DATES}` | YAML list of stakeholder-specific lockouts | see config below |
| `${PM_DRAFT_MODEL}` | (optional) override LLM for client-comms drafting | `openai-codex/gpt-5.4` (default agent model) |

## Install sequence

### Step 1 — Workspace rules (one-time, idempotent across projects)

**Operator:** From this repo, rsync the workspace add-on. Skip if `workspace/PROJECTS.md` already exists.

```bash
rsync -av workspace/PROJECTS.md ~/.openclaw/workspace/PROJECTS.md
```

**Remote:** Add to AGENTS.md boot sequence — append `PROJECTS.md` as step 8 (after BOOTSTRAP). Idempotent check first.

```bash
grep -q "PROJECTS.md" ~/.openclaw/workspace/AGENTS.md || \
  sed -i '' '/BOOTSTRAP.md/a\
8. **PROJECTS.md** — active client projects this agent is PM for (loaded after BOOTSTRAP, before HEARTBEAT)
' ~/.openclaw/workspace/AGENTS.md
```

### Step 2 — Per-project config (repeat for each project)

**Operator:** Create the project config directory and config file.

```bash
mkdir -p ~/.openclaw/projects/${PROJECT_SLUG}
cat > ~/.openclaw/projects/${PROJECT_SLUG}/config.yaml <<EOF
slug: ${PROJECT_SLUG}
display_name: "OpenLiteracy Phase 2 — Lesson & Remediation Pathways"
client: "OpenLiteracy"
status: active
contract:
  start: "2026-04-27"
  estimated_end: "2026-08-01"
  sprint_count: 3
  sprint_duration_days: 14
  current_sprint: 1
  current_sprint_ends: "2026-05-22"

repo:
  path: "${PROJECT_REPO}"
  remote: "origin"
  branch: "main"
  pull_before_work: true
  push_after_work: true

source_of_truth:
  registry: "${REGISTRY_PATH}"
  worklog: "WORKLOG.md"
  claude_md: "CLAUDE.md"
  parallel_views:
    - path: "docs/02-open-questions/README.md"
      label: "Eric's technical view"
      canonical: false
    - path: "docs/02-open-questions/01-questions-for-ol-team.md"
      label: "Eric's Round-1 batch"
      canonical: false

stakeholders:
  - name: "Ryan Shuken"
    role: dev
    side: us
    primary_channel: telegram
    chat_id: TBD
  - name: "Eric Schneider"
    role: dev-lead
    side: us
    primary_channel: telegram
    chat_id: TBD
  - name: "Beth Kukla"
    role: pm
    side: us
    primary_channel: telegram
    chat_id: TBD
    approval_gate: true   # all client-facing drafts route here first
  - name: "Sarah Scott Frank"
    role: ceo
    side: client
    primary_channel: email
    email: TBD
    chase_pattern: "next-morning"   # Beth's documented pattern
    notes: "Tends to spew ideas; needs structured questions. 'It feels very vague' is the failure mode."
  - name: "Rebecca Patterson"
    role: program-lead
    side: client
    primary_channel: email
    email: TBD
    timeline_constraint:
      unavailable_from: "2026-06-01"
      unavailable_to: "2026-08-01"
      reason: "London trip"
    notes: "Source of truth for curriculum logic. ALL her answers must lock by 2026-05-29."
  - name: "Greta Phillips Kendall"
    role: content-lead
    side: client
    primary_channel: email
    email: TBD
    deliverables:
      - "Learning Plan UI mockups by Sprint 2 week 1 (~2026-05-29)"

fathom:
  filter_attendees:
    - "sarah.scott.frank@openliteracy.com"
    - "rebecca.patterson@openliteracy.com"
    - "greta@openliteracy.com"
  filter_title_substrings:
    - "OL"
    - "OpenLit"
    - "Learning Pathways"
  meeting_folder_template: "docs/00-source/meetings/{date}_{slug}/Transcript_&_Recording/fathom-transcript.md"

cadence:
  morning_standup:
    enabled: true
    cron: "0 8 * * *"     # 8am local daily
    channel: telegram
    recipients: [ryan, beth]
  fathom_poll:
    enabled: true
    cron: "5 * * * *"     # every hour at :05
  question_staleness_check:
    enabled: true
    cron: "0 */4 * * *"   # every 4 hours
    stale_after_hours: 36
  deadline_watch:
    enabled: true
    cron: "0 6 * * *"     # 6am local daily
    warn_days_before: 7   # alert when 7 days from a stakeholder lockout

comms_autonomy:
  level: "drafts-only"  # alternatives: "send-unless-objected-1h", "auto-non-sarah", "full-auto"
  approval_topic: "${APPROVAL_TG_TOPIC}"
  approver: "Beth Kukla"
  draft_model: "${PM_DRAFT_MODEL:-openai-codex/gpt-5.4}"
  tone_guardrails: "workspace/projects/${PROJECT_SLUG}/comms-tone.md"

risks:
  - id: R1
    description: "Rebecca leaves 6/1, back 8/1. All her answers must lock by 5/29."
    mitigation: "Front-load Rebecca-owned questions in Round 1. Flag any STATUS=open by 5/22."
    severity: critical
  - id: R2
    description: "Two parallel question registries (Beth's 128 vs Eric's ~50). Unreconciled."
    mitigation: "Beth's is canonical for Flyn. Eric's tracked as 'technical view'. Reconciliation owner TBD."
    severity: high
  - id: R3
    description: "Sarah expects feature work; Sprint 1 is planning-only."
    mitigation: "Flyn never frames sprint output as 'a wiki' to Sarah. Always 'detailed master plan'."
    severity: medium
EOF
```

### Step 3 — Deploy helper scripts (one-time, idempotent)

**Operator:** Sync the PM scripts.

```bash
mkdir -p ~/.openclaw/scripts/flyn/pm
rsync -av deploy/pm/*.py ~/.openclaw/scripts/flyn/pm/
chmod +x ~/.openclaw/scripts/flyn/pm/*.py
```

### Step 4 — Initial registry sync (one-time per project)

**Remote:** Bootstrap Graphiti with the current state of the question registry.

```bash
python3 ~/.openclaw/scripts/flyn/pm/registry_parser.py \
  --project ${PROJECT_SLUG} \
  --bootstrap
```

This parses the canonical registry markdown, extracts each question as a typed fact, and POSTs to Graphiti `/api/episode`. Run once at install; subsequent runs are diff-only.

### Step 5 — Register cron pulses

**Remote:** Install the four crons. Each one routes to the same dispatcher script, which reads the per-project config and runs the relevant task.

```bash
openclaw cron add --name pm-morning-standup-${PROJECT_SLUG} \
  --cron "0 8 * * *" \
  --command "python3 ~/.openclaw/scripts/flyn/pm/morning_standup.py --project ${PROJECT_SLUG}"

openclaw cron add --name pm-fathom-poll-${PROJECT_SLUG} \
  --cron "5 * * * *" \
  --command "python3 ~/.openclaw/scripts/flyn/pm/fathom_router.py --project ${PROJECT_SLUG}"

openclaw cron add --name pm-question-staleness-${PROJECT_SLUG} \
  --cron "0 */4 * * *" \
  --command "python3 ~/.openclaw/scripts/flyn/pm/registry_parser.py --project ${PROJECT_SLUG} --staleness-check"

openclaw cron add --name pm-deadline-watch-${PROJECT_SLUG} \
  --cron "0 6 * * *" \
  --command "python3 ~/.openclaw/scripts/flyn/pm/morning_standup.py --project ${PROJECT_SLUG} --deadline-only"
```

### Step 6 — Verify

**Remote:**

```bash
# Confirm crons are registered
openclaw cron list | grep "pm-.*-${PROJECT_SLUG}"

# Verify Graphiti has questions
curl -sS "http://localhost:8100/api/search?q=${PROJECT_SLUG}+open+question" | jq '.facts | length'

# Test the morning standup manually
python3 ~/.openclaw/scripts/flyn/pm/morning_standup.py --project ${PROJECT_SLUG} --dry-run
```

## Operating model

### What Flyn does autonomously

- **Reads + writes** the GitHub planning repo (per-project repo path in config). Pulls before reading, commits + pushes after meaningful changes. Follows the repo's own CLAUDE.md rules.
- **Mirrors registry state** to Graphiti so cross-project queries work ("which projects have unresolved questions owned by a stakeholder leaving this month?").
- **Posts to operator Telegram** (Ryan + Beth at minimum) — morning standup, new transcript notifications, escalation pings.
- **Drafts client communications** and posts them to the approval topic. Never sends.

### What Flyn requests approval for (always)

- Any outbound message to a `side: client` stakeholder. Drafted, never auto-sent at launch (`comms_autonomy.level = drafts-only`).
- Any change to the canonical registry (`source_of_truth.registry`) — Flyn proposes a diff via Telegram, operator approves before commit.
- Any new project added to `workspace/PROJECTS.md`.
- Any cron schedule change.

### What Flyn does NOT do

- Decide which registry is canonical when there's ambiguity. Operator decides; Flyn follows.
- Force reconciliation of parallel views (Beth's vs Eric's). Tracks both, surfaces conflicts, lets humans resolve.
- Spawn ad-hoc meetings. Suggests async question batches instead — that's the philosophy from the 5/06 transcript.
- Speak in Sarah/Rebecca/Greta's voice. Drafts are in Beth's voice (or Ryan's, configurable per stakeholder).

## Talking to Flyn about a project

These prompts are how the operator interacts with the PM capability from Telegram:

- **"What's blocking Sprint 2 for OpenLit?"** → Flyn queries Graphiti for `(Question)-[STATUS]->(open)` AND `dependency_sprint=2` for the project, returns sorted by owner.
- **"What did Sarah say about exit tickets in the 5/11 kickoff?"** → Flyn queries Graphiti for episodes mentioning "exit ticket" with attendee Sarah, returns timestamped quotes.
- **"Draft a chase email to Rebecca for L-04 and L-05"** → Flyn pulls the question text from the registry, drafts in Beth's voice, posts to approval topic.
- **"Mark Q.P.3 as answered: 'Group exit tickets are once-per-week, not daily'"** → Flyn updates the registry MD in the repo, commits with cite-the-why message, ingests new episode to Graphiti, replies with the diff URL.

## Failure modes

- **Graphiti down:** Flyn falls back to grep over the registry markdown directly. Slower but functional. Logs to `~/.openclaw/logs/pm-graphiti-down-{date}.log`.
- **Fathom MCP not available:** Flyn skips auto-ingestion, flags to operator at next standup. Operator can manually file transcripts and run `fathom_router.py --manual <file>`.
- **Two parallel registries diverge:** Flyn posts a diff to Telegram tagged `RECONCILE` — does not auto-merge.
- **Operator approval not received:** Drafts stay in the approval topic. After 24h, Flyn re-pings once. After 48h, escalates the operator's operator (Ryan, if Beth is the primary approver).
- **Repo push rejected (merge conflict):** Flyn aborts the operation, logs the conflict, pings operator. Never blindly resolves (per the repo's CLAUDE.md rule).

## Migration notes

This skill explicitly supersedes ad-hoc PM patterns in earlier deploys:

- **deploy-asana.md** — for clients who use Asana, this skill writes through Asana via that integration; otherwise GitHub-repo-only.
- **deploy-content-pipeline.md** — overlaps for transcript ingestion; this skill is project-aware where content-pipeline is generic.
- **deploy-fathom-pipeline.md** — provides the Fathom polling base; this skill consumes it and adds project routing.

See `_archive/` if you find an older `deploy-pm-*.md` referenced anywhere; those predate the multi-project config pattern.
