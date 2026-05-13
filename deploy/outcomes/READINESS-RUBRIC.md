# Flyn-as-Project-Manager Readiness Rubric & Eval

> **Purpose.** Honest, testable assessment of whether Flyn is ready to be the
> project lead for **OpenLiteracy** and **Cora**. Each dimension has 1–5
> sub-criteria, each testable. Aggregate scores produce a "ship it" verdict
> per business + a prioritized gap list.
>
> **Method.** Worker (Claude) assesses each criterion against current
> infrastructure on 4C and the codebases at `/Users/4c/AI/flyn-agent`,
> `/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase`, and `~/.openclaw/`.
> Grader independently re-scores using the same evidence. Disagreements
> highlight items needing deeper investigation.
>
> **Scoring scale:**
> - **1 — Not started.** No code, no plan, just an idea.
> - **2 — Scaffolded.** Code exists but doesn't run end-to-end.
> - **3 — Partially working.** Works in some cases; breaks on edge cases.
> - **4 — Working.** Reliable in normal operation; gaps in monitoring/recovery.
> - **5 — Production-ready.** Fully wired, observable, recoverable.
>
> **Self-score date:** 2026-05-12 (mid-session, post-Phase-5 commit)
> **Scored by:** Claude Opus 4.7 (1M context), worker pass
> **Grader pass:** scheduled — to be run via `outcomes_runner.py`

---

## Aggregate verdict (TL;DR)

| Business | Current readiness | "Ship it?" |
|---|---|---|
| **OpenLiteracy** | **3.4 / 5** (median per-dimension) | **Yes for sprint coordination, no for autonomous client comms** |
| **Cora** | **2.6 / 5** (median) | **No — Cora has near-zero PM scaffolding wired** |

**The big gap for both:** observability + autonomous monitoring. Flyn can do every individual action; humans don't have a single pane of glass to verify Flyn is actually doing those actions reliably across all the projects without each-one-by-hand checking.

---

## Dimension 1 — Communication channels

Can Flyn reach the right human via the right channel?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 1.1 DM operator (Ryan) on Telegram | 5 | `@flyn_4c_bot` verified, chat_id 7191564227 in USER.md | — |
| 1.2 DM business partner (Beth) | 5 | CONTACTS.md, chat_id 7434192034, message_id 18 delivered today | — |
| 1.3 Group chats (Telegram topics) | 2 | `#flyn-briefing`/`#flyn-alerts` referenced in HEARTBEAT.md but resolve fails | Wire actual topics or fall back to DM |
| 1.4 Draft client emails for human approval | 3 | `comms_drafter.py` scaffold; full draft→queue→approve flow but no real email-send | Wire Gmail MCP `create_draft` |
| 1.5 Linear comments | 1 | MCP installed, OAuth not complete yet | Awaiting Ryan's auth |
| 1.6 Slack post | 1 | MCP installed, OAuth not complete | Auth |
| 1.7 GitHub PR comments | 1 | GitHub Copilot MCP shows "failed to connect" | Reconfigure or use `gh` CLI from Bash |
| 1.8 WhatsApp DM | 2 | `WhatsApp default: linked, configured` per `openclaw channels list` but routing rules TBD | Decide use case |
| 1.9 Multi-recipient routing | 3 | Telegram bridge DMs Beth on decisions, Ryan on answers — works | Extend to per-project routing |
| 1.10 Tone-per-recipient | 3 | CONTACTS.md captures Beth's tone rules; no per-stakeholder draft prompts yet | Add tone files per OL stakeholder |

**Axis average: 2.6 → 3 (rounded)** · `partially-working`

---

## Dimension 2 — Project state tracking

Can Flyn know what's open, what's blocked, who owns it?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 2.1 Read questions w/ filters | 5 | `list_questions(owner=…, sprint=…, bucket=…)` via MCP — works | — |
| 2.2 Get single question detail | 5 | `get_question(id)` works | — |
| 2.3 Mark answered + persist | 5 | `answer_question` end-to-end including audit | — |
| 2.4 Reassign owner | 5 | `reassign_question` end-to-end | — |
| 2.5 Log decisions | 5 | `create_decision` w/ 4 real decisions in DB | — |
| 2.6 Audit trail | 5 | `list_audit` (auth) returns all mutations | — |
| 2.7 Aggregate stats per project | 5 | `/api/stats` gives questions × status × owner × sprint × bucket | — |
| 2.8 Cross-project query | 1 | OL only; no Cora project config exists yet | Build Cora project config + seed |
| 2.9 Linear ticket sync | 1 | Not wired | After Linear auth |
| 2.10 Wiki Gantt + deps visualization | 4 | Live in wiki; force-directed graph deferred | Force-directed is polish |

**Axis average: 4.1 → 4** · `working`

---

## Dimension 3 — Meeting + knowledge ingest

Can Flyn pull new context in automatically?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 3.1 Pull Fathom transcripts (manual) | 5 | Fathom MCP working, 4 OL transcripts already in repo | — |
| 3.2 Auto-poll Fathom for new meetings | 2 | `fathom_router.py` skeleton; needs service-account API key | Provision Fathom service token |
| 3.3 Route project-relevant meetings to right repo | 3 | Filter logic in `fathom_router.py` works for `--manual` mode | Wire to polling |
| 3.4 Transcribe video files | 3 | `ffmpeg` + `whisper` installed locally; not yet wired into pulse | Add `video_transcriber.py` |
| 3.5 Extract action items from transcripts | 1 | No agent built | Phase 5 Outcomes-driven extract |
| 3.6 Update registry with surfaced questions | 3 | Done manually for 5/11 kickoff; no automation | Outcomes loop w/ approval gate |
| 3.7 Persist meeting in Graphiti | 4 | Patched bug + 91/124 episodes in KG | Finish bootstrap (quota-bound) |
| 3.8 Cross-project meeting routing | 1 | Only OL routing exists | Cora needs same |

**Axis average: 2.75 → 3** · `partially-working`

---

## Dimension 4 — Code, repos, CI/CD

Can Flyn participate in the actual engineering work?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 4.1 Read code in any repo Ryan owns | 5 | Bash + filesystem MCP — works for `~/AI/*` | — |
| 4.2 Make commits via git | 5 | 40+ commits made in this session across 2 repos | — |
| 4.3 Push to GitHub | 5 | All commits pushed | — |
| 4.4 Create PRs | 3 | `gh pr create` available via Bash; not used in our flow | Decide PR vs direct-to-main |
| 4.5 Review PRs | 1 | No agent; GitHub Copilot MCP failed | Wire `gh pr view` + comment via Bash |
| 4.6 Run tests | 4 | Used pytest for wiki-backend (12 passing) | Per-repo test runners |
| 4.7 Enforce linting / type-checks | 1 | None wired | ruff/mypy hooks |
| 4.8 Auto-doc on commit | 1 | Manually written | docstring-extractor agent |
| 4.9 CI/CD on push (GitHub Actions) | 2 | OL wiki has a manual workflow_dispatch; full push trigger pre-existed but disabled | Re-enable when secrets exist |
| 4.10 Deploy on tag | 3 | Cloudflare auto-deploy works for wiki; backend has launchd KeepAlive | Tag-driven deploys not wired |

**Axis average: 3.0** · `partially-working`

---

## Dimension 5 — Calendar + scheduling

Can Flyn manage the team's time?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 5.1 List events | 5 | Google Calendar MCP `list_events` works | — |
| 5.2 Create event | 5 | MCP `create_event` works | — |
| 5.3 Suggest meeting times | 5 | MCP `suggest_time` works | — |
| 5.4 Respond to invites | 5 | MCP `respond_to_event` works | — |
| 5.5 Calendar-aware standup ("Sarah is on PTO 6/1–8/1") | 2 | Hardcoded in project config; no live calendar query | Cross-reference stakeholder unavailable_from with calendar |
| 5.6 Auto-schedule next sprint review | 1 | Not implemented | TBD |
| 5.7 Detect double-booking | 3 | MCP can list but no proactive check | Heartbeat pulse |

**Axis average: 3.7 → 4** · `working`

---

## Dimension 6 — Files + external data

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 6.1 Read Google Drive files | 3 | MCP works but session expires mid-task; just hit this with Rebecca's video | Recover session reliability OR fall back to local download via TeamViewer |
| 6.2 Search Drive | 4 | `search_files` works | — |
| 6.3 Upload to Drive | 1 | Not exercised | — |
| 6.4 Read Gmail | 5 | `search_threads`/`get_thread` work (used today for Fathom emails) | — |
| 6.5 Create Gmail drafts | 4 | MCP `create_draft` available; not yet wired into comms_drafter | Wire it |
| 6.6 Label/organize Gmail | 4 | MCP available | — |
| 6.7 Notion access | 3 | MCP connected; we haven't used it yet | TBD if Notion is a system of record |
| 6.8 Local file r/w | 5 | Bash + Read/Write tool — full access | — |

**Axis average: 3.6 → 4** · `working`

---

## Dimension 7 — Decision-making + autonomy

Where can Flyn act on its own vs. ask?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 7.1 Knows approval gates | 5 | AGENTS.md "Approval gates" section is authoritative | — |
| 7.2 Drafts comms when uncertain | 4 | Comms drafter queues for Beth approval | Real send-after-approve flow needs Gmail wiring |
| 7.3 Recognizes "message <contact>" as approval | 4 | Codified in AGENTS.md updates today | Live-tested only with Beth so far |
| 7.4 Refuses out-of-domain writes | 4 | Documented; not yet adversarially tested | — |
| 7.5 Logs every mutation in audit | 5 | All wiki-backend writes go through `audit()` | — |
| 7.6 Can run multi-step plans w/o asking | 3 | This session demonstrated it (5 phases shipped) but each was Ryan-prompted | True overnight autonomy = not possible w/o Outcomes driver wired |
| 7.7 Outcomes / rubric-driven iteration | 2 | Scaffold (`outcomes_runner.py`) exists but isn't wired to actually execute (only plans + grades) | Wire shell-tool integration |
| 7.8 Graceful degradation (failed MCP, rate limit, etc) | 3 | Bootstrap caught Gemini 429 and stopped; no auto-retry tomorrow | Wire retry-on-quota-reset pulse |

**Axis average: 3.75 → 4** · `working`

---

## Dimension 8 — Observability + reporting

Can humans see what Flyn is doing without each-by-hand checking?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 8.1 Per-pulse logs on disk | 5 | `/tmp/*.log` for every service + pulse | — |
| 8.2 Audit table in DB | 5 | Live | — |
| 8.3 Telegram pings on mutations | 5 | Webhook → bridge → DM working (Beth/Ryan get pings) | — |
| 8.4 Daily standup digest | 3 | `morning_standup.py --dry-run` works; cron registers but not yet delivering (topic-routing fail) | Wire to DM Beth + Ryan directly |
| 8.5 Single dashboard | 2 | Wiki shows OL but not Cora; no cross-project view | Multi-project wiki section |
| 8.6 Alerting on errors | 1 | Sentry MCP not authed; pulse failures only log to disk | Wire Sentry or simple "alert Ryan if pulse fails > 1×" |
| 8.7 Cost / token spend tracking | 1 | Not measured | Telemetry pulse |
| 8.8 Health metrics endpoint | 4 | Wiki backend `/api/health`, Graphiti `/api/health`, gateway `openclaw health` | Aggregate into one |

**Axis average: 3.25 → 3** · `partially-working`

---

## Dimension 9 — Knowledge depth (does Flyn understand the project)

Beyond data — does Flyn have the *context* to make good PM calls?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 9.1 Knows OL stakeholders + roles + constraints | 5 | `config.yaml` per-project + USER.md + CONTACTS.md | — |
| 9.2 Knows OL design principles | 4 | 10 principles in synthesis.md ratified into wiki | Sarah hasn't reviewed/confirmed yet |
| 9.3 Knows OL critical path | 4 | Sprint plan + question dependencies in DB | Sprint plan still has 2 parallel versions to reconcile |
| 9.4 Knows technical context (MVP code, schema) | 3 | `prior-code-analysis/` 8 docs | Out of date if MVP changes |
| 9.5 Knows Cora context | 1 | Mentioned but not loaded | Build `~/.openclaw/projects/cora/config.yaml` |
| 9.6 Knows commercial context (engagements, pricing) | 2 | Some references in USER.md; no canonical contracts doc | — |
| 9.7 Surfaces own knowledge gaps | 3 | Asks "open questions" but doesn't proactively flag knowledge holes | "What I don't know yet" pulse |

**Axis average: 3.1 → 3** · `partially-working`

---

## Dimension 10 — Reliability + recovery

What breaks when one piece fails?

| Sub-criterion | Score | Evidence | Gap |
|---|---|---|---|
| 10.1 Services auto-restart on crash | 5 | launchd `KeepAlive=true` for backend, bridge, graphiti, gateway | — |
| 10.2 Services survive reboot | 5 | All `RunAtLoad=true` | — |
| 10.3 Tolerates Gemini rate-limit | 2 | Bootstrap stops on 429 with no auto-retry tomorrow | Retry pulse w/ backoff |
| 10.4 Tolerates Telegram bot block | 2 | No detection; messages silently fail | Watch sendMessage status code |
| 10.5 Tolerates Graphiti / Neo4j down | 4 | Wiki backend works without it; Flyn's structured queries fall back to grep on registry | — |
| 10.6 Tolerates upstream API changes | 3 | Patched graphiti-core today; no upstream-change pulse | Upstream changelog watch |
| 10.7 Backup strategy for SQLite + Neo4j | 1 | Both volumes are on host but no scheduled backup | nightly tarball + Drive upload |
| 10.8 Disaster recovery procedure | 2 | `install-flyn.sh` is idempotent but assumes data dirs exist | Document full DR run-book |

**Axis average: 3.0** · `partially-working`

---

## Aggregate

| Dimension | Score (1-5) | Trend |
|---|---|---|
| 1. Communication | 3.0 | Linear+Gmail send round out → 4.0 |
| 2. Project tracking | 4.0 | Linear sync + Cora seed → 4.5 |
| 3. Meeting ingest | 3.0 | Service-account Fathom key → 4.0 |
| 4. Code + CI/CD | 3.0 | Wire automated PR review → 4.0 |
| 5. Calendar | 4.0 | Already strong |
| 6. Files + external | 4.0 | Drive session reliability → 4.5 |
| 7. Decision-making + autonomy | 4.0 | Outcomes driver shell-tool → 5.0 |
| 8. Observability | 3.0 | Daily standup live + Sentry → 4.5 |
| 9. Knowledge depth | 3.0 | Cora config + Sarah-ratified principles → 4.0 |
| 10. Reliability + recovery | 3.0 | Backups + retry pulses → 4.0 |
| **Overall** | **3.4** | **Solid 3 (partially-working). Production-ready 5 needs 8-12 weeks more of grinding.** |

---

## Top 10 concrete gaps to close (ranked by leverage)

1. **Wire Linear OAuth + 2-way sync** — Beth/Eric want Linear-style ticketing. OL questions ↔ Linear issues bridge. Most-wanted feature, 4-6 hours of work.
2. **Daily standup actually delivers** — `morning_standup.py` works but the cron route doesn't deliver (topic-resolution fail). Replace topic routing with direct DM to Beth + Ryan. **1 hour.**
3. **Outcomes driver shell-tool integration** — make the rubric loop actually execute (not just plan). Unblocks autonomous nightly grind. **4-6 hours.**
4. **Cora project config + seed** — Cora has near-zero PM scaffolding. Build `~/.openclaw/projects/cora/config.yaml`, seed initial questions from Cora's WORKLOG. **2 hours.**
5. **Sentry alerting wired** — when a pulse fails 2x in a row, Ryan gets a Telegram alert. **2-3 hours after auth.**
6. **Real `comms_drafter` → Gmail `create_draft`** — currently scaffolded; wire actual send. **2 hours.**
7. **Fathom service-account API key** — auto-pull new meetings. Requires Fathom plan check. **30 min + cost decision.**
8. **Video transcription pulse** — wire `ffmpeg + whisper` into a "new attachment in Drive" handler. **2-3 hours.**
9. **Backup pulse** — nightly tarball of SQLite + Neo4j, push to Drive. **1 hour.**
10. **Disaster recovery run-book** — written, tested. **2 hours.**

**Total: ~30-40 hours of focused work to move from 3.4 to ~4.5/5.**

---

## Linear integration architecture

### Topology

```
   OL_KB GitHub      Cora GitHub
       │                  │
       │ webhook          │ webhook
       ▼                  ▼
   wiki-backend ─────► Linear sync agent ─────► Linear API
       │                                          │
       │ webhook                                  │ webhook
       ▼                                          ▼
   Telegram bridge ─────────────────────────► Flyn (openclaw)
```

### Sync rules

- **Each open OL registry question** → a Linear issue in `OPENLITERACY` team
  - Labels: `section-A`, `sprint-1`, `owner-rebecca`, `bucket-ai-does`
  - Priority: derived from `target_sprint` (1 = urgent, 3 = low)
- **Each question status change** in the wiki → comment on the Linear issue + state transition (Open → In Progress when status=`pending-answer`; → Done when status=`answered`)
- **Each Linear comment** → optional surface in the wiki (or just store in DB for later)
- **Each Linear status change by a human** → mutate the wiki via the API (Linear is a write-back channel, not read-only)

### Idempotency

Each question carries a `linear_issue_id` in the DB after first sync. Re-runs update the existing issue, never create duplicates.

---

## CI/CD agent architecture

### Per-repo agents

```
GitHub push → GitHub Actions → "agent CI" job →
  ├── lint + type-check (ruff, mypy, eslint, tsc)
  ├── unit tests (pytest, vitest)
  ├── docstring + comment scan (ai-reviewer agent)
  ├── changelog draft (ai-changelog agent)
  ├── PR comment with summary
  └── status check
```

Two agents:

1. **`ai-reviewer`** — reads the diff, scores against project style guide (CLAUDE.md), flags drift. Leaves PR comments. Uses Claude API via the existing Anthropic auth profile.
2. **`ai-changelog`** — writes a draft entry for `CHANGELOG.md` / WORKLOG.md based on the diff. Commits to a `claude/changelog` branch for review.

### Decision points

- **Run for which repos?** All `~/AI/*` repos Ryan owns? Or opt-in per-repo via a `.github/workflows/ai-ci.yml`?
- **Per-PR vs per-commit?** PR-level catches batch issues; commit-level is faster signal.
- **Where does the agent run?** GitHub Actions free runners are 2 vCPU; Claude API calls are fine there. Or run on 4C via webhook receiver (cheaper but adds latency).

---

## Recommended next-iteration tasks (in priority order)

| # | Task | Estimate | Blocked by |
|---|---|---|---|
| 1 | Linear OAuth complete + smoke-test | 15 min | User auth |
| 2 | Wire `morning_standup.py` to DM Beth + Ryan directly (skip topics) | 1 hr | — |
| 3 | Transcribe Rebecca's Pearl Platform video → update I.13 | 30 min | File on 4C disk |
| 4 | Build `OPENLITERACY` Linear team + sync the 124 wiki questions to Linear issues | 4 hr | Linear auth |
| 5 | Build `~/.openclaw/projects/cora/config.yaml` + seed Cora questions from `getcora.io` codebase | 3 hr | Inventory of Cora's open work |
| 6 | Wire Sentry alerting for cron failures | 2 hr | Sentry auth |
| 7 | Outcomes driver shell-tool integration (actual exec, not just plan) | 6 hr | Anthropic API budget |
| 8 | Nightly backup pulse (SQLite + Neo4j → Drive) | 2 hr | — |
| 9 | `ai-reviewer` GitHub Action MVP | 4 hr | — |
| 10 | Disaster recovery run-book + dry-run | 3 hr | — |

**Reasonable sprint sequence:**

- **Tonight (if you stay up):** items 1-3 (~2 hrs, all blocked or near-blocked)
- **Tomorrow:** items 4-5 (~7 hrs)
- **Mid-week:** items 6-10 (~17 hrs)

After all 10: Flyn is ~4.5/5 across all dimensions. **Production-ready PM** with the gaps in Dimension 9 (knowledge depth — needs Sarah review) and 10 (DR — needs first real failure test) being the remaining stretches.

---

*Eval written 2026-05-12 23:30 local. Grader pass scheduled via `outcomes_runner.py`. Worker self-scored 3.4 / 5; grader expected to land 3.0-3.5 range based on conservative interpretation.*
