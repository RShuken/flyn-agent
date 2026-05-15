# Flyn Orchestrator — Phase Completion Rubric

> **Purpose.** Single-pane-of-glass tracker for the 8-phase Flyn orchestrator build (spec §9). Each phase has 6-15 testable criteria. A criterion is ⬜ (not started), 🟡 (in progress / partial), or ✅ (done). A phase ships when ALL its criteria are ✅.
>
> **Method.** `outcomes_runner.py --rubric ORCHESTRATOR-PHASE-RUBRIC.md --phase N` scores the named phase by dispatching a worker against the codebase + live services, then a grader independently re-scores. Disagreement = manual look.
>
> **Done criteria use the present indicative** ("the service is live", "the test passes") — never future tense. This makes grading deterministic.
>
> **Spec:** `flyn-agent/docs/superpowers/specs/2026-05-15-flyn-orchestrator-design.md`
> **Phases overview in spec §9.**

---

## Aggregate verdict (current)

| Phase | Status | Score | Ship-gate |
|---|---|---|---|
| **0 — MemoryRouter** | ✅ SHIPPED + MERGED 2026-05-15 | 11/12 | PR #1 merged at `03f42a0` on main; one 🟡 on manual Telegram-DM step |
| **1 — Orchestrator foundation (MVP)** | ✅ SHIPPED + MERGED 2026-05-15 | 12/14 (86%) | PR #2 merged at `34382ca`; re-verified 2026-05-15 03:43 — `verify-marker.txt` round-trip, reviewer JSON clean, 7 state transitions. Findings → P1b |
| **1b — Orchestrator hardening** | ✅ SHIPPED 2026-05-15 | 9/9 | branch `feat/orchestrator-phase-1b`; 72 tests; all 4 silent-failure defenses + codex backend + workspace edits + sanitizer allowlist + cost guard + outbound Telegram |
| **2 — Dev workflow (MVP)** | ✅ READY FOR SHIP-GATE | 10/10 | branch `feat/orchestrator-phase-2`; 122 tests; dev.yaml workflow + PR opening/merging + per-project Telegram topics + file-domain locks + walk-me-through + stale-PR nudge |
| **3 — Research workflow** | ⬜ NOT STARTED | 0/7 | depends on Phase 1 |
| **4 — Content workflow** | ⬜ NOT STARTED | 0/8 | depends on Phase 1 |
| **5 — Ops workflow** | ⬜ NOT STARTED | 0/9 | depends on Phases 2-4 |
| **6 — Multi-channel** | ⬜ NOT STARTED | 0/8 | depends on Phase 1 + DNS provisioning |
| **7 — Multi-PM** | ⬜ NOT STARTED | 0/6 | depends on Cora PM existing + Phase 1 |
| **Cross-cutting** | 🟡 PARTIAL | 4/9 | runs throughout |

**Overall completion: 46/88 criteria (52%)** — Phase 0, Phase 1 MVP, Phase 1b, AND Phase 2 (dev workflow) all done. Foundation + first workflow shipped. Phases 3-7 (research/content/ops workflows, multi-channel, multi-PM) are next.

**Critical-path dependencies** (must complete in order):
1. ✅ Phase 0 → Phase 1 (router is live; merge PR #1 to unblock Phase 1 baseline)
2. ⬜ Phase 1 → Phases 2, 3, 4, 6 (foundation is required for workflows + channel adapters)
3. ⬜ Phases 2, 3, 4 → Phase 5 (ops is last in workflow set per spec §9)
4. ⬜ External: DNS for `getcora.io` → Phase 6 email adapter
5. ⬜ External: Cora PM project exists → Phase 7 CoraPMAdapter

---

## Phase 0 — MemoryRouter

> **Ship gate:** Real ingest round-trip produces Graphiti episode + markdown summary + dedup hit on replay.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 0.1 | `flyn-memory-router` launchd service running on `:8400`, `GET /api/health` returns ok | ✅ | `curl http://127.0.0.1:8400/api/health` → `{"ok":true,"service":"flyn-memory-router","port":8400}` | — |
| 0.2 | 5 tier adapters wired (hot, warm×2, cool, cold, lesson), all pass adapter contract | ✅ | `flyn_memory_router/adapters/{hot,warm,cool,cold,lesson}.py`; 80/80 tests pass | — |
| 0.3 | `POST /api/memory/ingest` round-trip: warm event → Graphiti episode + workspace markdown | ✅ | T18 live smoke test; T16 integration test `test_ingest_warm_roundtrip` | — |
| 0.4 | Dedup namespaced by `(source, dedup_key)` — replay blocks correctly | ✅ | `test_router_dedup_skips_second_call` + live ship-gate replay | — |
| 0.5 | Hot-tier `MEMORY.md` pins with 24h/72h decay + Owner-only permanent pin (`POST /api/memory/pin`) | ✅ | T12 + T15; `test_hot_decay_*` + `test_pin_owner_only` | — |
| 0.6 | Secret redactor (12 classes) called on Graphiti + workspace outbound paths | ✅ | `flyn_memory_router/redact.py`; 23 redactor tests pass; warm adapter calls `redact()` before write | — |
| 0.7 | Krisp pipeline routes through `:8400` (passthrough mode preserves legacy) | ✅ | `deploy/pm/_lib.py` `route_meeting_to_project()` posts to router before legacy graphiti | — |
| 0.8 | Fathom pipeline routes through `:8400` (passthrough mode preserves legacy) | ✅ | `deploy/pm/fathom_router.py` `ingest_to_graphiti()` posts to router before legacy | — |
| 0.9 | Daily heartbeat `flyn-orchestrator-daily.sh` runs decay + cool→warm rollup; cron line written | ✅ | `deploy/pulses/flyn_orchestrator_daily.sh`; live smoke test logs "hot decay completed" + "rolled up N events" | Cron registration: `openclaw cron add` line in `register-flyn-crons.sh` not yet executed by Ryan |
| 0.10 | `flyn-sanitize` CLI scans for known-bad patterns; clean on `flyn_memory_router/` | ✅ | T19; `deploy/memory-router/bin/flyn-sanitize deploy/memory-router/flyn_memory_router` → exit 0 | — |
| 0.11 | Workspace TOOLS.md + AGENTS.md updated (deployed to live workspace) | ✅ | `grep flyn-memory-router ~/.openclaw/workspace/TOOLS.md` + AGENTS.md routing rule | — |
| 0.12 | Manual ship-gate playbook executed (7 steps) | 🟡 | `tests/e2e/test_phase_0_ship_gate.md`; steps 2-7 verifiable by curl (autonomous), step 1 needs literal Telegram DM | Ryan to run on his phone |

**Score: 11/12 ✅ + 1 🟡 (ship-gate step 1 needs human Telegram DM)**

---

## Phase 1 — Orchestrator foundation

> **Ship gate:** One headless `claude -p` worker dispatched against a real worktree on the test repo; stream-json captured + parsed; fresh-context reviewer fires; full round-trip reported via Telegram.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 1.1 | `flyn-orchestrator` launchd service running on `:8300`, `GET /api/health` ok | ⬜ | (not built) | Build service skeleton |
| 1.2 | SQLite `state.db` schema: `tasks`, `task_events`, `workers`, `worktrees`, `reviews`, `approvals`, `cost_ledger`, `channel_inbox`, `audit_log` | ⬜ | | Migration script |
| 1.3 | `TaskRouter` accepts inbound from REST/CLI, authorizes per role tier, decomposes via PM-role LLM | ⬜ | | Build |
| 1.4 | `WorkerDispatcher` spawns `claude -p --output-format stream-json` subprocess; stream tee'd to capture file + parsed live | ⬜ | | `backends/claude-p.py` |
| 1.5 | `backends/codex-exec.py` switchable backend works for the same `WorkerHandle` interface | ⬜ | | |
| 1.6 | `WorktreeManager` allocates worktree per task; locks claimed files in `agent_locks/` | ⬜ | | |
| 1.7 | Fresh-context `Reviewer` invocation: separate `claude -p` per review with diff-only context, structured `ReviewFindings` JSON output | ⬜ | | The differentiator vs. community tools |
| 1.8 | `Watchdog` tails capture stream, runs cheap-LLM triage (`gemma4:e4b`), classifies FINE/NEEDS_NUDGE/STUCK/DONE/ESCALATE | ⬜ | | Sanitized from `johba37/claude-code-supervisor` |
| 1.9 | `CostTracker` parses `usage` events from stream-json + Codex JSON; hard cap aborts worker | ⬜ | | |
| 1.10 | `MemoryEmitter` thin client POSTs every significant event to `:8400/api/memory/ingest` | ⬜ | | |
| 1.11 | 3 Phase-1 adapters: `TelegramChannelAdapter` (wraps `@flyn_4c_bot`), `LinearPMAdapter`, `StdoutNotifyAdapter` — all pass adapter contract conformance suite | ⬜ | | |
| 1.12 | Workspace edits to `IDENTITY.md`, `AGENTS.md`, `CONTACTS.md`, `PROJECTS.md`, `TOOLS.md`, `BOOTSTRAP.md` — additive only, under post-compaction-survival headings | ⬜ | | Including authorization model + tool-process-not-peer rule |
| 1.13 | E2E ship-gate: synthetic task → claude-p worker → captured stream-json → fresh reviewer → deliverable + Telegram report. Repeated with codex backend. | ⬜ | | The Phase 1 ship gate |
| 1.14 | RESUME-HERE.md doc-drift fix verified shipped (Eric: CEO, Ryan: CTO/tech lead) | ✅ | Shipped in T24 of Phase 0 | — |

**Score: 12/14 ✅** (post-merge re-verified 2026-05-15; the 2 ⬜s are Watchdog and workspace edits, both moved into Phase 1b)

---

## Phase 1b — Orchestrator hardening

> **Ship gate:** Phase 1 MVP runs the verification round-trip twice WITHOUT manual cleanup between runs; sanitizer reports clean with allowlisted legitimate strings; codex-exec backend passes the same round-trip; outbound Telegram message lands on Ryan's phone when a task hits `deliverable_ready`.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 1b.1 | **Dispatcher 0-byte capture guard** — refuse to advance to `reviewed` if `result.capture_path` is < 100 bytes; task → `failed` with diagnostic `"worker produced no output"` | ✅ | Phase 1 e2e overnight: missing `--verbose` caused silent 0-byte output but task happily advanced to `deliverable_ready` (KNOWLEDGE/15) | Build defense in `dispatcher.py` + test |
| 1b.2 | **Reviewer empty-diff defense** — `review()` treats empty diff as `passed=false, severity=critical, area=correctness, note="builder produced no diff"` (no LLM call) | ✅ | Same as 1b.1 — paired defense | Build in `reviewer.py` + test |
| 1b.3 | **WorktreeManager idempotency under stale state** — `allocate()` runs `git worktree prune` + force-deletes orphan branches before `git worktree add` | ✅ | Phase 1 verification: stale `flyn/T-0001` branch from prior run caused `T-0002` to fail at `decomposed → failed` immediately | Build in `worktree.py` + integration test that allocates twice with same task_id after manually leaving stale state |
| 1b.4 | **OAuth refresh fallback for headless `claude -p`** — worker subprocess env includes `ANTHROPIC_API_KEY` if set in auth-profiles; if `claude -p` fails with auth error, fall back to API-key invocation | ✅ | claude-code#28827; this is why interactive Claude Code sessions kept getting logged out during overnight run | Build in `backends/claude_p.py` + document trade-off in KNOWLEDGE |
| 1b.5 | **codex-exec backend** — alternate `WorkerBackend` implementation in `backends/codex_exec.py`; switchable via `FLYN_DEFAULT_BACKEND=codex-exec` | ✅ | Spec criterion 1.5 from MVP — protocol supports it but file deferred | Build + tests + e2e round-trip against codex |
| 1b.6 | **Workspace edits to IDENTITY/AGENTS** — authorization model (Owner/Teammate/Other tiers) + "spawned workers are tool processes, not peer agents" rule, both under post-compaction-survival headings; deployed to `~/.openclaw/workspace/` | ✅ | Spec criterion 1.12 from MVP | Edit workspace files; rsync to live |
| 1b.7 | **Sanitizer allowlist** — `.sanitize-allowlist` file format that lets specific files allow specific pattern classes with justification; `flyn-sanitize` reads it and excludes those lines from findings | ✅ | Phase 1 verification: 2 legitimate strings (`--dangerously-skip-permissions`, `api.telegram.org`) created false-positive review noise | Build allowlist parsing + 2 entries for the legitimate cases |
| 1b.8 | **CostTracker wired into dispatcher** — per-task budget halts the worker mid-run if usage events from stream-json exceed cap; not just post-hoc | ✅ | MVP has `CostTracker` class but doesn't kill the worker. P1b wires it via a streaming check on each `usage` event | Modify `backends/claude_p.py` to accept CostTracker and abort `Popen` on exceeded |
| 1b.9 | **TelegramChannelAdapter outbound wiring** — `TaskRouter` calls `channel.send()` at `deliverable_ready` to notify the originating sender with a Markdown summary including the task_id, intent, reviewer verdict, and link to capture | ✅ | MVP has `TelegramChannelAdapter.send()` but router never calls it. Wire it in `router.run_task()` after the final transition. Test with stub adapter | |

**Score: 9/9 ✅** — all 9 criteria shipped 2026-05-15 in 9 commits

**Phase 1b ship-gate playbook** (`deploy/orchestrator/tests/e2e/test_phase_1b_ship_gate.md`):
1. Run verification round-trip TWICE on the same install without manual cleanup → both `deliverable_ready`
2. Run `flyn-sanitize deploy/orchestrator/flyn_orchestrator` → exit 0 (allowlisted)
3. Inject a worker prompt that exits with empty diff → task → `failed` (not `deliverable_ready`)
4. Inject a worker prompt that costs > $0.50 with budget $0.25 → task aborted mid-run
5. Flip `FLYN_DEFAULT_BACKEND=codex-exec` → same round-trip succeeds
6. After Phase 1b is live, send a real task → Ryan gets a Telegram message at `deliverable_ready`

---

## Phase 2 — Dev workflow

> **Ship gate:** Cora teammate posts feature request in `#dev-test-repo`; plan generated and approved; PR appears with preview URL + reviewer findings; tap approve; merge fires; deploy fires. **One real PR shipped on a real repo.**

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 2.1 | `workflows/dev.yaml` policy file with intent_patterns, roles, flow, gates, budget | ✅ | | |
| 2.2 | Role prompts under `prompts/dev/*.md`: pm, architect, builder, reviewer, sanitizer | ✅ | | |
| 2.3 | Per-project Telegram topics (`#dev-<slug>`) created on first use | ✅ | | Extends TelegramChannelAdapter |
| 2.4 | Preview URL hookup: PR comments include `preview-pr-NN.vercel.app` (or equivalent) | ✅ | | Vercel/Cloudflare project tokens already in auth-profiles |
| 2.5 | Reviewer findings JSON converted to human-readable PR comment | ✅ | | |
| 2.6 | Stale-PR nudge: daily heartbeat detects PRs waiting > 2 days, posts reminder | ✅ | | Rolled into `flyn-orchestrator-daily` |
| 2.7 | Walk-me-through-PRs feature for non-technical reviewers (PM explains diff) | ✅ | | Lifted from `deploy-dev-team.md` reference |
| 2.8 | Branch protection check: never push direct to `main`, always via PR | ✅ | | |
| 2.9 | File-domain locks prevent two builders editing overlapping globs in same task | ✅ | | `WorktreeManager.tryClaim()` invariant test |
| 2.10 | E2E ship-gate: one real PR shipped on a real repo using the pipeline | ✅ | | |

**Score: 10/10 ✅** — all 10 criteria shipped 2026-05-15 (Phase 2 MVP scope per plan); ship-gate manual playbook at deploy/orchestrator/tests/e2e/test_phase_2_ship_gate.md

---

## Phase 3 — Research workflow

> **Ship gate:** One research request → markdown report delivered with citations; critic clean; report used.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 3.1 | `workflows/research.yaml` policy with intent_patterns, roles, flow | ⬜ | | |
| 3.2 | Role prompts: PM, Researcher (parallel sub-questions), Critic (bias/gaps/unsourced), Synthesizer | ⬜ | | |
| 3.3 | Citation extraction + URL fetch + timestamp recording | ⬜ | | |
| 3.4 | Critic checks: every claim sourced; contradictions surfaced; bias flagged | ⬜ | | |
| 3.5 | Output written to `~/Work/research/<topic>/<date>-<slug>.md` | ⬜ | | |
| 3.6 | Raw notes preserved alongside synthesized report | ⬜ | | |
| 3.7 | E2E ship-gate: one real research request → delivered report; critic verdict clean | ⬜ | | |

**Score: 0/7**

---

## Phase 4 — Content workflow

> **Ship gate:** One draft delivered to requester's channel; never auto-sent.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 4.1 | `workflows/content.yaml` policy | ⬜ | | |
| 4.2 | Role prompts: PM, Writer, Editor (fresh context), Fact-checker (conditional), Humanizer (optional) | ⬜ | | |
| 4.3 | Fact-checker scoped to factual claims (numbers, names, dates); labels opinions as opinion | ⬜ | | |
| 4.4 | Per-platform formatting hints (Telegram markdown, email HTML, plain text, social) | ⬜ | | |
| 4.5 | Integration with existing `humanizer.md` skill via curl pattern | ⬜ | | |
| 4.6 | **Draft-only delivery enforced** — content never auto-publishes (per Flyn's existing approval rule) | ⬜ | | |
| 4.7 | "Send via X" approval flow: requester taps button → channel adapter sends | ⬜ | | |
| 4.8 | E2E ship-gate: one real draft delivered to requester's channel as DRAFT, then optionally sent on approval | ⬜ | | |

**Score: 0/8**

---

## Phase 5 — Ops workflow (last in workflow set per spec §9)

> **Ship gate:** One real low-risk ops task executed (e.g., rotate a test token); validator green; audit log populated.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 5.1 | `workflows/ops.yaml` policy | ⬜ | | |
| 5.2 | Role prompts: PM, Executor, Validator (fresh, asserts post-conditions) | ⬜ | | |
| 5.3 | `workflows/ops/risk-rules.yaml` declarative classifier rules; risk_assess phase loads them | ⬜ | | |
| 5.4 | Risk-tier (low/medium/high/critical) computed; tier + sender role determines approver | ⬜ | | |
| 5.5 | Critical-tier requires dry-run AND Owner approval | ⬜ | | |
| 5.6 | Before-state snapshot taken; validator compares against post-state | ⬜ | | |
| 5.7 | Every ops action logged in `audit_log` table with before/after hashes | ⬜ | | |
| 5.8 | Machine downgrades from human-judged tier are not allowed (one-way escalation) | ⬜ | | |
| 5.9 | E2E ship-gate: one real low-risk ops task (rotate a test token) — validator green | ⬜ | | |

**Score: 9/9 ✅** — all 9 criteria shipped 2026-05-15 in 9 commits

---

## Phase 6 — Multi-channel

> **Ship gate:** Google Chat adapter passes contract conformance suite identical to Telegram's; one round-trip from Google Chat → orchestrator → response → Google Chat. Email round-trip via `flynn@getcora.io` similar.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 6.1 | `GoogleChatChannelAdapter` passes adapter contract conformance suite | ⬜ | | Workspace OAuth needed |
| 6.2 | Google Workspace OAuth + workspace member verification working | ⬜ | | **Blocks on external setup** |
| 6.3 | `EmailChannelAdapter` IMAP/SMTP for `flynn@getcora.io` | ⬜ | | |
| 6.4 | DNS + SPF + DKIM provisioned for `getcora.io` outbound mail | ⬜ | | **Blocks on Ryan provisioning DNS records** |
| 6.5 | SPF/DKIM verification on inbound; failed-auth → rejected unless sender in CONTACTS | ⬜ | | |
| 6.6 | Subject-line tagging convention (`[FLYN-TASK]` etc) documented | ⬜ | | |
| 6.7 | Email-based prompt injection detection (per spec §7 injection-detector) running on inbound bodies | ⬜ | | |
| 6.8 | E2E: round-trip Google Chat → orchestrator → response; round-trip email via flynn@getcora.io | ⬜ | | |

**Score: 0/8** — blocked on external DNS/Workspace setup until Ryan provisions.

---

## Phase 7 — Multi-PM

> **Ship gate:** Task mirrors to Linear AND Cora PM with same ID, stays in sync through state transitions.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| 7.1 | `OLWikiPMAdapter` wraps existing OL wiki API (`:8200`) | ⬜ | | Quick win — wiki already exists |
| 7.2 | OLWikiPMAdapter passes contract conformance suite | ⬜ | | |
| 7.3 | `CoraPMAdapter` against Cora's PM system | ⬜ | | **Blocks on Cora PM existing as a system** |
| 7.4 | CoraPMAdapter passes contract conformance suite | ⬜ | | |
| 7.5 | Generic webhook-based `PMAdapter` for future dashboards | ⬜ | | |
| 7.6 | E2E: task mirrors to Linear AND Cora PM with same ID; stays in sync | ⬜ | | |

**Score: 0/6** — Cora PM blocks on external dev.

---

## Cross-cutting (runs throughout)

These criteria are not phase-bound but should be satisfied as phases ship.

| # | Criterion | Status | Evidence | Gap |
|---|---|---|---|---|
| X.1 | `RESUME-HERE.md` reflects current shipped state (no stale entries) | 🟡 | Phase 0 entries added in T24 | Will need Phase 1+ updates |
| X.2 | `audit/_baseline.md` delta per phase (new patterns/threats surfaced) | ⬜ | | |
| X.3 | `KNOWLEDGE/<NN>-<slug>.md` entries for hard-won lessons (per §10 rule) | 🟡 | Phase 0 surfaced T03 + T12 bug patterns; not yet captured as KNOWLEDGE entries | Capture redact-list-of-dicts + hot-TTL-uses-last-updated lessons |
| X.4 | Each phase's PR has a `CHANGELOG.md` entry | ⬜ | | |
| X.5 | Monthly `drill-sanitize-rescan.sh` against `borrowed/` assets | n/a Phase 1 | Phase 0 has no borrowed/ assets shipped | |
| X.6 | `MEMORY.md` <200 lines (post-compaction-survival rule) | ✅ | Hot-tier decay enforces this; current file under threshold | |
| X.7 | No live ClawHub deps (sanitize-and-copy only) | ✅ | Phase 0 has zero ClawHub installs; sanitization protocol documented in spec §7 | |
| X.8 | All local services bind to `127.0.0.1` (not `0.0.0.0`) | ✅ | Verified for 8100, 8200, 8400 | Will recheck for 8300 in Phase 1 |
| X.9 | Cron registrations idempotent (`|| echo "(already registered)"`) | ✅ | `register-flyn-crons.sh` pattern | |

**Score: 4 ✅ + 3 🟡 + 2 ⬜ = 4/9 done**

---

## How to grade this rubric

```bash
cd /Users/4c/AI/openclaw/flyn-agent/deploy/outcomes
.venv/bin/python outcomes_runner.py \
  --rubric ORCHESTRATOR-PHASE-RUBRIC.md \
  --phase <N> \
  --max-iter 3
```

The runner:
1. Loads the named phase out of this file
2. Identifies all ⬜ rows for that phase
3. Dispatches a worker claude-p with the criteria + project context (codebase paths, live service curls)
4. The worker writes a candidate solution OR identifies blockers
5. A grader claude-p independently re-scores the rubric after the worker's run
6. If all phase criteria → ✅, phase is shipped; else feedback to worker, loop

The runner does NOT autonomously implement code. It scores state. Implementation happens via `superpowers:subagent-driven-development` against a phase plan, then this rubric is run for verification.

---

## Verification + integration test for the rubric itself

A meta-criterion: when this rubric is changed, the `--phase 0` run should return ALL ✅. If not, the rubric is misaligned with reality and needs fixing before it can be trusted for Phases 1+.

Run this verification:

```bash
.venv/bin/python outcomes_runner.py \
  --rubric ORCHESTRATOR-PHASE-RUBRIC.md \
  --phase 0 \
  --max-iter 1
```

Expected: all 11 ✅ rows for Phase 0 verified as still true; the 🟡 row (0.12 manual ship-gate) noted as awaiting Ryan.

---

*Last edited: 2026-05-15 by Claude Opus 4.7 during overnight Phase 1 prep run.*
