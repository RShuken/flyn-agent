# Changelog — flyn-agent

All notable changes to the Flyn orchestrator and memory router. Entries are reverse-chronological. Each entry references its merged PR (`gh pr view <N>`).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with a `## [PR-N] YYYY-MM-DD` heading per release. Versions are PR-numbered rather than semver because the orchestrator ships continuously.

## [Unreleased]

Pending work — see `RESUME-HERE.md` "Phase 6/7 remaining buildable-without-blockers".

---

## [PR #11] 2026-05-16 — Phase 7 partial: OLWiki + Webhook PMAdapters

### Added
- `flyn_orchestrator/adapters/pm/olwiki.py` — wraps OL wiki `POST /api/decisions` at `:8200`. Maps `TaskRecord.intent` → Decision row.
- `flyn_orchestrator/adapters/pm/webhook.py` — generic JSON POST adapter for future PM systems; optional `X-Flyn-Secret` header.
- `flyn_orchestrator/adapters/pm/_http.py` — shared stdlib urllib helper (no new deps; injectable for tests).
- `tests/unit/test_pm_adapter_conformance.py` — 29 parametrized tests over Linear / OLWiki / Webhook covering Protocol `isinstance`, name/configured, all 4 methods, best-effort HTTP guarantee.
- `tests/unit/test_olwiki_adapter.py` — 12 OLWiki-specific tests.
- `tests/unit/test_webhook_adapter.py` — 16 Webhook-specific tests.

### Changed
- Phase 7 rubric criteria 7.1, 7.2, 7.5 → ✅. Overall rubric 70/87 → 73/87 (84%).

### Notes
- **Adapter best-effort guarantee** formalized: every PMAdapter method must NOT raise. HTTP failures stub-return (`olwiki-stub-<task_id>` etc) so an upstream outage never cascades to task failure. See `KNOWLEDGE/20-adapters-never-raise.md`.
- State transitions are no-ops in MVP (OL wiki has no native task-state field); deferred to Phase 7b.

---

## [PR #10] 2026-05-16 — Rubric audit + drift fixes

### Fixed
- Phase 1 rows 1.1-1.13 were still ⬜ despite Phase 1 MVP merged 2026-05-15. Marked ✅ with evidence pointers; 1.8 (Watchdog) confirmed genuinely unbuilt (grep). Score 12/14 → 13/14.
- Phase 5 aggregate vs detail mismatch (aggregate said 9/9, detail 8/9 + 1 🟡). Synced.
- Phase 6 score line was a copy-paste artifact (claimed "8/8 ✅ shipped 2026-05-15" but every row ⬜). Fixed to "0/8 ⬜".
- Cross-cutting count `4 ✅ + 3 🟡 + 2 ⬜` → `4 ✅ + 2 🟡 + 2 ⬜ + 1 n/a = 4/8`.
- Overall denominator: 88 → 87 (X.5 is n/a in current scope).

---

## [PR #9] 2026-05-16 — Phase 2c cleanup: remove `_handle_ops_approval` shim

### Removed
- 24-line `_handle_ops_approval` shim from `TaskRouter`. The shim existed only to support a single integration test calling the pre-refactor private method.

### Changed
- `test_critical_tier_owner_only` rewritten to use the public `router.handle_approval(task_id, ApprovalDecision(...))` API. `gate="teammate"` → teammate role, `gate="critical"` → owner role.
- Stale docstring in `test_pr_lifecycle.py` (referenced deleted `_run_dev_pr_phase`) updated to document the actual cross-module mocking strategy.
- Phase 2 ship-gate playbook backlog entry for "router refactor" struck through.
- `router.py`: 578 → 554 lines.

### Notes
- See `KNOWLEDGE/19-test-the-public-api-not-internals.md` for the principle.

---

## [PR #8] 2026-05-16 — Phase 2c: router refactor → 4 phase modules

### Added
- `flyn_orchestrator/phase_services.py` — frozen 11-field `PhaseServices` dataclass bundling shared dependencies.
- `flyn_orchestrator/research_phase.py` — 5-step parallel-researcher pipeline (117 lines).
- `flyn_orchestrator/content_phase.py` — 8-phase content pipeline + send-via-X approval (243 lines).
- `flyn_orchestrator/ops_phase.py` — risk-tier pipeline + audit log + tier-keyed approval (408 lines).
- `flyn_orchestrator/dev_phase.py` — PR push + open + merge on approval (197 lines).

### Changed
- `flyn_orchestrator/router.py`: **1,398 → 578 lines** (−820). `TaskRouter` is now the state-machine coordinator and approval dispatcher only; per-workflow logic lives in phase modules.
- `test_pr_lifecycle.py`: patches updated to cover `dev_phase.subprocess.run` (cross-module mocking).

### Notes
- Pure structural refactor. All 190 pre-existing tests pass byte-for-byte unchanged; 2 new unit tests for `PhaseServices` → 192 total.
- Two cross-module mock-patching issues caught during T05 (subprocess + `pr.create_pr`). See `KNOWLEDGE/18-cross-module-mock-patching.md`.

---

## [PR #7] 2026-05-15 — Phase 5: ops workflow

### Added
- `flyn_orchestrator/workflows/ops.yaml` + `workflows/ops/risk-rules.yaml` — declarative risk-tier classifier with 4 tiers × ~3 rules.
- `flyn_orchestrator/prompts/{pm_ops,risk_classifier,executor,validator}.md` — 4 role prompts.
- `flyn_orchestrator/risk_tier.py` — rule loader + `classify_intent_by_rules` + `max_tier` (one-way escalation guard).
- `flyn_orchestrator/audit.py` — SnapshotBundle + SHA256 hashing for file/http/cmd targets; `snapshot_target` + `verify_target_changed`.
- `flyn_orchestrator/ops.py` — 5 orchestration functions + 6 dataclasses.
- `state.py`: `audit_log` table (UNIQUE on task_id+action+ts) + `append_audit` + `list_audit`.
- `TaskRouter._run_ops_phase` + `_execute_ops_and_finalize` + `_handle_ops_approval` — tier-keyed approval (low: auto / medium-high: owner-or-teammate / critical: owner-only + written rationale).
- `TaskState` gains `AWAITING_OWNER_APPROVAL` + `REJECTED`.

### Notes
- **One-way escalation**: LLM downgrade attempts clamped to rule floor via `max_tier(llm_tier, rule_floor)`. Machines can never downgrade a human-judged tier.
- Test count: 161 → 190.

---

## [PR #6] 2026-05-15 — Phase 4: content workflow

### Added
- `flyn_orchestrator/workflows/content.yaml` + 5 role prompts (pm_content, writer, editor, fact_checker, humanize_invoker).
- `flyn_orchestrator/content.py` — orchestration: spec → draft → edit → fact-check → humanize.
- `flyn_orchestrator/formatting.py` — per-platform formatting (telegram/email/slack/plain/tweet/linkedin/markdown).
- `TaskRouter._run_content_phase` — draft-only by default; explicit "send via X" → `FINAL_APPROVAL_PENDING` → teammate approval → TelegramChannelAdapter.send.

### Notes
- Test count: 141 → 161.

---

## [PR #5] 2026-05-15 — Phase 3: research workflow

### Added
- `flyn_orchestrator/workflows/research.yaml` + 4 role prompts (pm_research, researcher, critic, synthesizer).
- `flyn_orchestrator/citations.py` — citation extraction + URL fetch + timestamp recording.
- `flyn_orchestrator/research.py` — PM decomposes intent into 2-4 sub-questions; N researchers run via `ThreadPoolExecutor` (cap 4); fresh-context Critic audits for unsourced / contradictions / bias / gaps; Synthesizer merges to Markdown.
- Output lands at `~/Work/research/<topic>/<date>-<slug>.md` with `raw/` JSON notes.

### Notes
- Test count: 122 → 141.

---

## [PR #4] 2026-05-15 — Phase 2: dev workflow (real PRs on real repos)

### Added
- `flyn_orchestrator/workflows/dev.yaml` + `prompts/pm_dev.md`.
- `flyn_orchestrator/pr.py` — `gh` CLI wrapper (`create_pr`, `merge_pr`, `pr_number_from_url`).
- `TaskRouter._run_dev_pr_phase` — push branch + open PR + transition to `FINAL_APPROVAL_PENDING`.
- `handle_approval` — `gh pr merge` on approve; cancelled on reject.
- TelegramChannelAdapter per-project forum topics (createForumTopic + slug cache).
- `flyn_orchestrator/locks.py` — file-domain LockManager.
- `flyn_orchestrator/walkthrough.py` — fresh-context PR walkthrough generator.
- `flyn-pr-nudge` daily stale-PR Telegram reminder.

### Notes
- Test count: 72 → 122.

---

## [PR #3] 2026-05-15 — Phase 1b: orchestrator hardening

### Added
- Dispatcher 0-byte capture guard (1b.1).
- Reviewer empty-diff defense (1b.2).
- WorktreeManager idempotency under stale state — `git worktree prune` + force-delete orphan branches before `allocate` (1b.3).
- `backends/codex_exec.py` — alternate `WorkerBackend` switchable via `FLYN_DEFAULT_BACKEND=codex-exec` (1b.5).
- Workspace edits to `IDENTITY.md` / `AGENTS.md` — 3-tier auth model + "spawned workers are tool processes, not peer agents" rule (1b.6).
- `.sanitize-allowlist` format + 2 entries for legitimate strings (1b.7).
- CostTracker mid-stream worker-kill on BudgetExceeded (1b.8).
- TelegramChannelAdapter outbound wiring — router calls `channel.send()` at `deliverable_ready` (1b.9).

### Fixed
- **OAuth token discrimination**: `_load_anthropic_api_key_from_profiles()` now only returns `sk-ant-api*` tokens. OAuth (`sk-ant-oat*`) returns None so the backend falls back to OAuth-via-credentials-cache (1b.4; commit `2ea787d`).

### Notes
- See `KNOWLEDGE/15` (`claude -p --verbose`), `KNOWLEDGE/16` (worktree stale state), `KNOWLEDGE/17` (OAuth refresh), `KNOWLEDGE/21` (token discrimination).
- Test count: 48 → 72.

---

## [PR #2] 2026-05-15 — Phase 1: orchestrator foundation (MVP)

### Added
- `flyn-orchestrator` launchd service on `:8300`; `flyn_orchestrator/server.py` REST.
- SQLite `state.db` schema: `tasks`, `task_events`, `workers`, `worktrees`, `reviews`, `approvals`, `cost_ledger`, `channel_inbox`.
- `flyn_orchestrator/router.py:TaskRouter` with full state machine spine: `INBOUND → TRIAGING → ROUTED → DECOMPOSED → DISPATCHED → RUNNING → REVIEWED → DELIVERABLE_READY`.
- `flyn_orchestrator/backends/claude_p.py` — `claude -p --output-format stream-json --verbose` subprocess; stream tee'd + parsed live for cost.
- `flyn_orchestrator/dispatcher.py` — `WorkerDispatcher` + `BackendRegistry`.
- `flyn_orchestrator/worktree.py` — `WorktreeManager` allocates per task.
- `flyn_orchestrator/reviewer.py` — fresh-context `claude -p` per review; structured `ReviewFindings` JSON.
- `flyn_orchestrator/cost.py` — `CostTracker` parses `usage` events.
- `flyn_orchestrator/memory.py` — `MemoryEmitter` thin client POSTing to `:8400`.
- 3 adapters: `TelegramChannelAdapter`, `LinearPMAdapter`, `StdoutNotifyAdapter`.

### Notes
- Test count: 0 → 48.
- Real e2e ship-gate PASSED on 4C — synthetic task T-0001 went inbound→deliverable_ready in 30 seconds with real `claude -p` worker (~$0.30) + fresh-context reviewer (~$0.15).

---

## [PR #1] 2026-05-15 — Phase 0: memory router

### Added
- `flyn-memory-router` launchd service on `:8400`.
- REST API: `/api/health`, `/api/memory/ingest`, `/api/memory/pin` (POST + DELETE), `/api/memory/maintenance/decay`.
- 5 tier adapters: hot, warm×2, cool, cold, lesson.
- Daily heartbeat (`flyn_orchestrator_daily.sh`) — decay + cool→warm rollup.
- Krisp + Fathom pipelines migrated in passthrough mode.
- `flyn-sanitize` CLI — 12-class secret redactor + scan.
- Workspace `TOOLS.md` + `AGENTS.md` routing rule edits.

### Notes
- Test count: 0 → 80.
- Live service confirmed running on 4C at `http://localhost:8400`.
- Manual ship-gate step 1 (real Telegram DM) pending Ryan.
