# Changelog — flyn-agent

All notable changes to the Flyn orchestrator and memory router. Entries are reverse-chronological. Each entry references its merged PR (`gh pr view <N>`).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with a `## [PR-N] YYYY-MM-DD` heading per release. Versions are PR-numbered rather than semver because the orchestrator ships continuously.

## [Unreleased]

Pending work — see `RESUME-HERE.md` "Phase 6/7 remaining buildable-without-blockers".

---

## [PR #29] 2026-05-18 — Daily heartbeat sweep for expired ops approvals

### Added
- `StateStore.list_tasks_by_state(state)` — new query method returning all `TaskRecord`s in a given state; used by the sweep, useful for future health-check endpoints.
- `ops_phase.sweep_expired_approvals(store, memory_emitter=None, *, now=None)` — pure helper (no PhaseServices coupling) that walks `AWAITING_OWNER_APPROVAL` tasks, checks per-tier expiry, writes `approval_expired` audit rows with `actor="sweep"`, and emits `ops_approval_expired` memory events.
- Daily heartbeat integration: `flyn_orchestrator_daily.sh` now calls `sweep_expired_approvals` so stale approvals are proactively rejected, not only at approval-arrival time.

### Notes
- Closes the "no active expiration job" threat raised in PR #28 §Δ.5b-approval-expiry.
- Low-risk tasks (auto-execute, no approval window) are unaffected.

---

## [PR #28] 2026-05-18 — Phase 5b: time-windowed ops approvals

### Added
- Per-tier approval expiry windows enforced at approval-arrival time: medium 2 h, high 1 h, critical 30 min (low: n/a, auto-executes).
- `ops_phase.run` records `approval_issued_at` (ISO-8601 UTC) on every transition to `AWAITING_OWNER_APPROVAL`; `approval_context` dict gains `issued_at` + `expires_after_seconds` for future UI rendering.
- Expired APPROVE attempts → `REJECTED` with `approval_expired` audit row; REJECT attempts always pass through (rejecting a stale request is always safe).

### Notes
- Stale critical approvals can no longer auto-execute hours later when operational context may have changed.

---

## [PR #27] 2026-05-18 — Registries auto-wire memory_emitter into adapters

### Added
- `PMRegistry`, `ChannelRegistry`, and `NotifyRegistry` gain optional `memory_emitter` constructor kwarg + `attach_memory_emitter()` retro-wire method.
- Adapters with a `_memory_emitter` slot are auto-wired on `register()`; explicit per-instance configuration always wins.
- Three usage modes: construction-time wiring, retro-wiring after bootstrap, per-instance override.

### Notes
- Closes the §Δ.adapter-observability threat "wiring is per-adapter-instance, not central".

---

## [PR #26] 2026-05-18 — MemoryEmitter into adapters: swallowed-error observability

### Added
- `flyn_orchestrator/adapters/_observability.py` — shared `emit_swallowed_error(memory_emitter, adapter_name, method, exc, *, task_id=None)` helper; no-op when `memory_emitter is None`; wraps `emit()` itself in try/except so a broken MemoryEmitter cannot break the adapter.
- The 4 I/O-performing adapters (OLWiki, Webhook, Telegram, Email) accept optional `memory_emitter` constructor kwarg; every swallowed HTTP/SMTP/IMAP error now fires an `adapter_swallowed_error` memory event.

### Notes
- Adapter never-raise contract preserved. Existing tests and callers see zero behavior change.
- Closes the KNOWLEDGE/20 observability gap.

---

## [PR #25] 2026-05-18 — Rubric: flip Phase 5.9 to ✅ (ship-gate Procedure C verified)

### Changed
- Phase 5 criterion 5.9 → ✅: Procedure C executed end-to-end on running `:8300` (2026-05-18).
- Phase 5 score: 8/9 + 1 🟡 → **9/9 ✅**.

### Notes
- Pure docs — no code changes.

---

## [PR #24] 2026-05-18 — Fix: map PermissionError→403 and ValueError→400 in /approve route

### Fixed
- `/approve` route only caught `NotImplementedError`; `PermissionError` and `ValueError` from `ops_phase` leaked through to uvicorn as HTTP 500. Now maps correctly to **403** (unauthorized approver) and **400** (empty rationale / bad input).

### Added
- 2 new integration tests; total test count: 394.

### Notes
- Follow-up to PR #23; together they make Phase 5 critical-tier gating fully correct.

---

## [PR #23] 2026-05-18 — Fix: owner-role from `FLYN_OWNER_IDENTIFIERS` env, not gate parameter (P5 security)

### Fixed
- **Security bug (P5):** `ops_phase.handle_approval` was deriving the approver's role from the caller-supplied `gate` parameter, allowing any teammate to approve a critical-tier ops task by sending `gate="critical"`. Role inference now uses `Config.owner_identifiers` (populated from `FLYN_OWNER_IDENTIFIERS` env, comma-separated emails).
- Falls back to empty frozenset when config is None → safe default: no one is owner.
- plist updated: `FLYN_OWNER_IDENTIFIERS=ryanshuken@gmail.com`.

### Notes
- Bug surfaced during Phase 5 ship-gate Procedure C (live run on `:8300`).

---

## [PR #22] 2026-05-18 — CONTACTS.md-driven email allowlist

### Added
- `flyn_orchestrator/adapters/channels/email_allowlist.py` — parses `## Email allowlist` section from `workspace/CONTACTS.md`; tolerates HTML comments, TBD placeholders, `-` and `*` bullets; case-insensitive.
- `workspace/CONTACTS.md` — new section listing `ryanshuken@gmail.com`, `beth@cora.community`, `eric@cora.community`; 3-step update runbook (edit → restart launchd → sanity-curl).
- `EmailChannelAdapter` uses CONTACTS.md as canonical allowlist source; `DEFAULT_ALLOWLIST` in `email.py` is now a fallback only.

### Notes
- Closes §Δ.6-partial threat "allowlist hardcoded vs CONTACTS.md".

---

## [PR #21] 2026-05-18 — ChannelAdapter conformance suite

### Added
- `tests/unit/test_channel_adapter_conformance.py` — 18 parametrized contract tests covering Protocol isinstance, name, `ingest(valid)` / `ingest(malformed)` / `ingest({})` return types, `send()` best-effort, and `approve_button()` best-effort for both `TelegramChannelAdapter` and `EmailChannelAdapter`.

### Notes
- Mirrors the PMAdapter conformance suite from PR #11.
- Both adapters already conformed; this adds defensive regression coverage. No rubric score change.

---

## [PR #20] 2026-05-18 — Phase 4b: content auto-rerun on editor/fact-check block

### Added
- `draft_content` gains `extra_context: Optional[str] = None` — appended to writer prompt with `---` separator on retry (same pattern as Phase 3b).
- `content_phase.run` on review-cycle failure: helper `_run_edit_and_factcheck` returns `(edit_result, fc_result, failed_at, blocking)`; if `failed_at` is not None, emits `content_retry_started` event, builds stage-typed retry context, and re-runs `draft_content` once with the gate's findings as additional context.

### Notes
- Symmetric counterpart to PR #19 (Phase 3b). Content workflow's editor-block and fact-checker-block paths are no longer dead-ends.

---

## [PR #19] 2026-05-18 — Phase 3b: research auto-rerun on critic block

### Added
- `run_researchers` gains `extra_context: Optional[str] = None` — when provided, appended (separated by `---`) to each researcher's prompt.
- `research_phase.run` on critic failure: builds a "Critic findings from previous research run" markdown block from blocking (critical/important) findings, emits `research_retry_started` memory event, cycles back through `REVIEWED → DISPATCHED → RUNNING` with `actor="research-retry"`, and re-runs `run_researchers` with the critic context.

### Notes
- Research workflow's critic-block path is no longer a dead-end — it auto-retries once. Closes a long-deferred Phase 3 backlog item.

---

## [PR #18] 2026-05-18 — Docs: cookbooks for extending the orchestrator

### Added
- `docs/cookbooks/README.md` — index + conventions + when to add a new cookbook.
- `docs/cookbooks/add-a-workflow.md` — 7-step build: policy YAML → role prompts → helper module → phase runner → router branch → tests → ship checklist.
- `docs/cookbooks/add-a-pm-adapter.md` — the 4-method Protocol contract, 3 invariants (best-effort, never-raise, state-mirroring-optional), step-by-step build with conformance-suite hookup.
- `docs/cookbooks/add-a-channel-adapter.md` — ingest/send/approve_button contract, when to add auth verification vs trust-the-channel, prompt-injection considerations.

### Notes
- Pure markdown, zero code impact. Locks in patterns from Phase 2c (workflows), Phase 7 (PMAdapter), Phase 6 (ChannelAdapter).

---

## [PR #17] 2026-05-18 — Watchdog default-on in TaskRouter

### Changed
- `TaskRouter.__init__` gains `watchdog_factory` (default `"default"` sentinel → installs built-in factory; `None` to disable; callable for custom wiring) and `triage_backend` (default `OllamaTriageBackend()` → gemma4:e4b; pluggable for tests) kwargs.
- New `_build_default_watchdog(capture_path, task_id, task_intent)` wires `on_nudge` → `worker_needs_nudge` memory event, `on_stuck` → `worker_stuck` memory event, `on_done` → `worker_done`.

### Notes
- Follow-up to PR #16. Watchdog was opt-in at `dispatch()` call site; now production dispatches get stuck-worker triage automatically.

---

## [PR #16] 2026-05-18 — Phase 1.8 Watchdog: stuck-worker triage

### Added
- `flyn_orchestrator/watchdog.py` — `Watchdog` polling daemon thread that tails worker capture files every 30 s, classifies via pluggable `TriageBackend` Protocol (`OllamaTriageBackend` using gemma4:e4b, `StubTriageBackend` for tests), emits FINE / NEEDS_NUDGE / STUCK / DONE / ESCALATE verdicts.
- Consecutive-STUCK threshold (default 2) provides hysteresis; ESCALATE bypasses threshold immediately.
- `WorkerDispatcher.dispatch()` gains opt-in `watchdog: Optional[Watchdog] = None` kwarg; bracketed start/stop in try/finally; existing callers unaffected.
- 15 unit tests + 3 integration tests; total: 343.

### Changed
- Rubric Phase 1: 13/14 → **14/14 (100%)**. Overall: 81/87 → **82/87 (94%)**.

---

## [PR #15] 2026-05-18 — Memory router: unified read surface (query/lint/sources/CLI)

### Added
- 10 async read adapters (hot, warm×2, cool, cold, lesson, reference, user, ol_wiki, ocw_mem, lossless) behind a common `ReadAdapter` Protocol (asymmetric from the write-side `MemoryAdapter`).
- REST endpoints: `POST /api/memory/query` (RRF rank fusion + dedup), `POST /api/memory/lint` (drift detection), `GET /api/memory/sources` (health per adapter).
- `flyn-mem` CLI: `query`, `health`, `sources`, `logs` subcommands with `--query-id` cross-file log correlation.
- `HealthTracker` with rolling 100-sample window per source (RLock-safe); daily JSONL rotation, 90-day/1 GB retention.
- Install script writes `/usr/local/bin/flyn-mem` symlink, auto-memory pointer, `TOOLS.md` section (all idempotent).
- **146 unit + integration tests** (34 commits; 80 pre-existing write-side tests preserved).

### Notes
- Extends existing Phase 0 memory router — same launchd unit, same `:8400` port. No sibling service.
- Raw chunks + citations come from the caller; no LLM in the router.

---

## [PR #14] 2026-05-16 — Audit baseline: per-phase deltas (closes X.2)

### Added
- `audit/_baseline.md` gains a `§Δ` section with 11 per-phase subsections (Δ.0 through Δ.7-partial + Δ.hygiene). Each subsection documents **New patterns** (positive contributions) and **New threats** (warnings to future work) for its phase.
- Going forward, every phase PR appends its own `§Δ.<phase-id>` block at merge time.

### Changed
- Cross-cutting rubric: X.2 → ✅. Score 80/87 → **81/87 (93%)**. Cross-cutting 7/8 → **8/8 (100%)**.

### Notes
- Pure docs — all 325 tests unchanged.
- Threat highlights: allowlist-hardcoded-vs-CONTACTS.md (Δ.6), injection-patterns-are-a-moving-target (Δ.6), rubric drift caught by audit PR #10 (Δ.hygiene).

---

## [PR #13] 2026-05-16 — Phase 6 partial: EmailChannelAdapter + SPF/DKIM + injection-detection

### Added
- `flyn_orchestrator/adapters/channels/email.py` — `EmailChannelAdapter` with injectable `smtp_sender`/`imap_fetcher` for tests; stub-mode when config absent. Adapter never raises — SMTP exceptions swallowed in `send`.
- `flyn_orchestrator/adapters/channels/email_auth.py` — RFC 8601 `Authentication-Results` parser + `verify_email_auth`; failed auth → ingest returns None unless sender is allowlisted.
- `flyn_orchestrator/adapters/channels/injection_detect.py` — 8 regex patterns covering instruction-override, role-reassignment, role-confusion, base64 smuggling, zero-width unicode, and excessive whitespace.
- `flyn_orchestrator/adapters/channels/email_subject.py` — TAG constants (`[FLYN-TASK]`, `[FLYN-REPLY:<id>]`, `[FLYN-APPROVE:<id>]`, `[FLYN-REJECT:<id>]`) + round-trip-stable `parse_subject`/`format_subject`.
- 76 new tests (`test_email_auth.py` ×17, `test_email_subject.py` ×14, `test_injection_detect.py` ×22, `test_email_adapter.py` ×23). Test count: 249 → 325.
- `docs/email-subject-tags.md` — user-facing convention reference.

### Changed
- Phase 6 rubric criteria 6.3, 6.5, 6.6, 6.7 → ✅. Score 76/87 → **80/87 (92%)**.

### Notes
- **Phase 6 still pending (blocked externally):**
  - **6.1 GoogleChatChannelAdapter** — blocked on Google Workspace OAuth provisioning.
  - **6.2 Google Workspace OAuth + member verification** — blocked.
  - **6.4 DNS + SPF + DKIM for `getcora.io`** — blocked on Ryan provisioning DNS records.
  - **6.8 E2E round-trip** — blocked on 6.1 + 6.4.
- Code is live-ready: once DNS TXT records land, flip `FLYN_EMAIL_SMTP_HOST` or add `email:flynn@getcora.io` to `auth-profiles.json` and the adapter ships mail.

---

## [PR #12] 2026-05-16 — Cross-cutting hygiene: KNOWLEDGE + RESUME-HERE + CHANGELOG

### Added
- `RESUME-HERE.md` — "Flyn Orchestrator — current state (2026-05-16)" section: phase shipping table (0–7), live services on 4C, auth-contention warning, manual ship-gates pending Ryan, 7 KNOWLEDGE entries from this build.
- 4 new KNOWLEDGE entries:
  - `18` — Cross-module mock patching (Phase 2c T05 — `subprocess.run` patches at `router.*` silently miss calls in extracted `dev_phase.*`).
  - `19` — Test the public API, not internals (Phase 2c-cleanup — private-method test forced a 24-line shim through refactor).
  - `20` — Adapters never raise (Phase 7 PMAdapter suite — HTTP failures must stub-return, not propagate to task `FAILED`).
  - `21` — OAuth vs API key token discrimination (loader was passing `sk-ant-oat-*` as `ANTHROPIC_API_KEY`; every worker failed silently).
- `CHANGELOG.md` — retroactive Keep-a-Changelog entries for PRs #1–#11.

### Changed
- Cross-cutting rubric: X.1, X.3, X.4 → ✅. Score 73/87 → **76/87 (87%)**. Cross-cutting 4/8 → **7/8**.

### Notes
- No code changes — pure docs. All 249 tests unchanged.

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
