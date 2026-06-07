# Session summary — 2026-05-20/21

A two-day work session that started with "fix conv-tier-1 broken HTTP path"
and ended with a full architectural cleanup of Flyn's personality layer.
Two PRs shipped (#38, #39), one postmortem written, one design spec
committed.

## What we did

### 1. Conv-Tier 2.0 — full rebuild (PR #38)

We diagnosed that conv-tier slice-1 (PR #37, still open) had architectural
shortcuts that would bite at scale: disk-glob polling, no supervision, no
backpressure, no end-to-end tracing, p50 latency 9.1s with 50% of that
being polling sleep.

Built conv-tier 2.0 from scratch in 9 phases:

| Phase | Deliverable |
|---|---|
| A | Design spec (`docs/superpowers/specs/2026-05-19-conv-tier-2.0-design.md`, 763 lines) + rubric |
| B | Workflow state machine + atomic transitions + migration (58 tests) |
| C | Async pipeline + durable work queue + worker pool + 4 real handlers (28 tests) |
| D | Observability — `/api/memory/conv/health`, Prometheus metrics, structured JSON logs |
| E | Backpressure — HIGH_WATER + drop policy + overload signal |
| F | Reliability — supervisor auto-restart, graceful drain, crash recovery |
| G | Test suite — load tests, chaos tests, handler unit tests |
| H | FastAPI wiring (`/api/memory/v2/ingest`) — shadow-mode alongside v1 |
| I | Live perf measurement on the running Mac mini |

Final live numbers: **e2e p50 = 73ms, p99 = 73ms** (target was < 2s/< 5s,
beat by 27×). Graphiti promotion was made async/best-effort because its
LLM-based entity extraction takes 60-180s per episode and would tie our
SLO to its uncontrolled latency.

255 tests total in the memory-router suite, all passing.

### 2. Discovered Flyn was misbehaving

Ryan reported a broken-link complaint to Flyn on Telegram. Flyn responded
with "Random pull from the memory stack" — running 7 memory queries
instead of addressing the broken link.

We traced through the agent loop and found Flyn was getting a **20,001-
character system prompt** every turn, most of it prescribing
memory-routing behavior:

| Source | Approx chars | Contribution |
|---|---|---|
| AGENTS.md | ~10,500 | Boot sequence + memory routing + approval gates + auth roles |
| IDENTITY.md | ~1,500 | Identity |
| SOUL.md | ~800 | Personality |
| USER.md + CONTACTS.md | ~1,200 | People profiles |
| TOOLS.md | ~1,800 | Tool descriptions (duplicating the tool registry) |
| MEMORY.md | ~1,900 | Hot-tier pinned facts |
| active-memory plugin | ~500 | "Memory recall policy" |
| lossless-claw plugin | ~1,800 | "Lossless Recall Policy" |
| **TOTAL** | **~20,000** | |

The 20k of "use memory tools first" was biasing the model toward memory
recall on every message — even on user complaints that needed direct
responses.

### 3. Did the postmortem

Traced `git log -- workspace/AGENTS.md` to provenance:

- **Seed:** commit `8b3e975` (2026-04-20) imported a "sanitized v2
  snapshot from a prior VC engagement" — a full PM-agent scaffold under
  `skills/_enterprise-v2-reference/` (deploy-daily-briefing-v2,
  deploy-action-items, deploy-urgent-email-v2, etc.).
- **NOT from ClaudeHub:** a `skills/clawhub-recommendations/` folder
  exists showing 18 ClawHub picks were *researched* but explicitly *not*
  used.
- **Growth pattern:** AGENTS.md went from 4258 chars (April 20) to
  10,466 chars (May 15) through 8 commits. The biggest jump was a
  commit literally named "Post-mortem cleanup: reshape flyn-agent around
  what actually works" — paradoxically net-added 3000 characters.
- **Template self-aware, ignored:** `templates/AGENTS.md` opened with
  "Target: under 200 lines, keep AGENTS.md focused on rules + boot
  sequence." The deploy + accretion violated its own constraint.

Full postmortem in `docs/remediation/2026-05-21-agent-personality-layer-postmortem.md`.

### 4. Designed the skill-index pattern (with Ryan)

Ryan's insight: the bootstrap files shouldn't be in the system prompt.
They should be **skills the agent calls**. The agent should know **what
skills exist** and **how to find them**, then load skill bodies only
when triggered.

This mirrors Claude Code's skill system + openclaw's own skills
infrastructure — neither of which we were using correctly.

### 5. Executed the cleanup (PR #39)

| Item | Before | After | Reduction |
|---|---|---|---|
| AGENTS.md | 10,466 chars | 2,265 chars | -78% |
| Total workspace bootstrap | ~50,000 chars | 8,334 chars (incl. openclaw stock defaults) | -83% |
| Response to "invalid link" | 50s, 7 queries, no answer | 25s, direct verified answer | ✓ |

What changed:

- **New lean `workspace/AGENTS.md`** — identity + voice + 5 hard rules +
  pointer to `skills/INDEX.md`. ~700 chars of actual rules.
- **New `workspace/skills/INDEX.md`** — markdown table mapping trigger →
  skill path. 6 on-demand skills + 11 cron skills + 10 lazy-load
  reference paths.
- **6 new on-demand skill bodies** with YAML frontmatter triggers:
  `memory-recall`, `broken-link-fix`, `message-contact`,
  `ship-gate-check`, `project-status-update`, `commitment-followup`.
- **Old bootstrap files archived** to `workspace/_archive/` for
  rollback. Then moved their content to `workspace/skills/_reference/`
  for lazy-load.
- **Disabled `active-memory` plugin** in `~/.openclaw/openclaw.json` —
  its prefetch-and-inject-memory behavior was the second-biggest source
  of bias.

### 6. Verified live

Same exact prompt that failed before now produces:

> "That link is invalid because the branch does not exist on
> `RShuken/flyn-agent` as of May 21, 2026.
>
> I verified two ways:
> - the URL returns 404
> - `git ls-remote --heads` doesn't include `feat/orchestrator-foundation-phase-1`
>
> Current remote branches: main, feat/conv-memory-telegram-slice-1,
> feat/conv-tier-2.0, fix/orchestrator-owner-identifiers-config,
> intel-mac-support, tune-outdoor.
>
> If you want, I can help identify which of those was meant to be the
> actual 'Phase 1' branch and give you the corrected link."

Direct. Verified. Offered the fix. No memory dump.

## What we discovered along the way

- **OpenClaw auto-restores stock template files** when bootstrap files
  are missing. We deleted IDENTITY/SOUL/USER/HEARTBEAT/TOOLS and they
  came back — but at tiny placeholder sizes (~200-1800 bytes each
  instead of the original 3-10k). Net effect: cleanup is durable.
- **Conv-tier 2.0 routes (`/api/memory/v2/ingest`, `/conv/health`,
  `/conv/metrics`) are mounted in the deployed memory-router** — they
  survived the chore-branch cleanup deploy because the chore branch
  also has the conv2 work in it.
- **The 20k systemPrompt floor is mostly platform-level.** Lossless-claw
  contributes ~3.5k, codex harness's built-in "Interaction Style" is
  ~12.7k. Those are out of our control without modifying upstream
  plugins. Our cleanup focused on what we control.
- **Graphiti is slow.** Direct measurement showed 200s+ for a single
  episode POST. This is why conv-tier 2.0 makes promote async/best-effort
  — blocking COMPLETE on Graphiti would tie pipeline e2e p99 to a
  downstream service we don't control.
- **The new pipeline's pickup latency is < 50ms** (verified by SLO
  test). Old polling-based daemon had 5-10s pickup latency built in.

## State of the world

### Branches & PRs

- `main` — stable
- `feat/conv-memory-telegram-slice-1` (PR #37) — slice-1 conv tier;
  WORKING in production but not yet merged. Superseded by #38 once both
  land.
- `feat/conv-tier-2.0` (PR #38) — the principled rebuild; live and
  validated. Ready to review/merge after a 24h soak.
- `chore/agent-personality-cleanup` (PR #39) — the personality-layer
  cleanup; live and validated. Ready to merge.

### Live services

- Memory router `:8400` — running, conv-tier 2.0 routes mounted, 10
  read adapters
- OpenClaw gateway — running, codex harness OAuth working, Telegram
  channel connected
- Graphiti `:8100` — running (slow on episode writes; ~200s)
- Ollama `:11434` — running, gemma4:e4b loaded
- Flyn (the agent) — responds naturally on Telegram, no memory dumps

### Pending manual work (Ryan-owned)

- 13-prompt test guide against `@flyn_4c_bot` (verify the cleanup
  end-to-end in real conversation patterns)
- 24h soak on conv-tier 2.0 before flipping the openclaw conv-tap
  plugin to POST to `/v2/ingest`
- Merge decision on #37 (slice-1) and #38 (2.0) — likely close #37
  without merging once #38 is validated

### Outstanding (not blocking)

- **Fix B** — Ollama embeddings for openclaw `memory-core` plugin
  (separate from conv tier; restores Flyn's natural-language "remember
  X" tool layer)
- The big enterprise-PM scaffolding under
  `skills/_enterprise-v2-reference/` and `skills/_archive/` can be
  pruned at any time — nothing references it in the new INDEX.md
- `lossless-claw` plugin still contributes ~3.5k of "Lossless Recall
  Policy" prompt. Its impact is now bounded by the new lean AGENTS.md.
  If it ever causes issues, the config knob is `enabled: false` in
  `openclaw.json`.

## Numbers to remember

- conv-tier 2.0 pipeline overhead: **73ms p50, p99** (27× headroom
  under SLO)
- bootstrap injection: **35,845 → 8,334 chars** (77% reduction)
- AGENTS.md: **10,466 → 2,265 chars** (78% reduction)
- Tests: **255 memory-router tests passing** (post-conv2 + cleanup)
- Two PRs ready for review: #38 (architecture) + #39 (agent cleanup)

## Reference docs created

- `docs/superpowers/specs/2026-05-19-conv-tier-2.0-design.md` — full
  design spec for the pipeline rebuild
- `docs/superpowers/plans/2026-05-19-conversation-memory-telegram-slice-1.md`
  — earlier slice-1 plan
- `docs/superpowers/plans/2026-05-21-agent-personality-cleanup.md` —
  this session's cleanup plan
- `docs/remediation/2026-05-19-conv-tap-and-memory-embeddings.md` —
  hook-pattern discoveries
- `docs/remediation/2026-05-21-agent-personality-layer-postmortem.md` —
  why the 20k bootstrap happened and what we did about it
- `deploy/outcomes/CONV-TIER-2.0-RUBRIC.md` — machine-gradable success
  criteria for the rebuild
