# Flyn MemoryRouter — Unified Ingest + Query Design

**Status:** Spec, awaiting user review
**Author:** Ryan Shuken + Claude (Opus 4.7, this session)
**Date:** 2026-05-16
**Relates to:** `2026-05-15-flyn-orchestrator-design.md` §2.5 (extends), `2026-05-15-flyn-memory-router-phase-0.md` (folds into; this work is added to that plan as Path A)
**Build path:** Local autonomous stack — `superpowers:subagent-driven-development` executing the extended Phase 0 plan, with `outcomes_runner.py` + a new `MEMORY-ROUTER-PHASE-0-RUBRIC.md` for per-phase verification.

---

## Section 1 — Goals, non-goals, success criteria

### What this is

A read surface for the existing write-only MemoryRouter (Phase 0). Same service, same port (`localhost:8400`), same launchd unit. Adds:

- A unified `POST /api/memory/query` endpoint that fans out across all of Ryan's memory systems
- A 6th-through-10th adapter family (`*_read.py`) — the reverse side of the existing write tiers, plus three new sources
- A `flyn-mem` CLI binary on `$PATH` so any agent (Flyn, Claude Code, scripts) can query via shell
- An optional `POST /api/memory/lint` endpoint for cross-source drift detection
- Auto-memory + workspace pointers so Claude Code in any cwd discovers the router automatically

### Why this exists (motivation)

Today Flyn reads from 4 fragmented sources (MEMORY.md, Graphiti, `openclaw memory search`, Lossless Claw) and Claude Code outside `flyn-agent/` reads from none of them. The new Karpathy reference vault at `~/AI/openclaw/reference/` adds a 5th. Each new memory system widens the gap, and asking "what does Ryan know about X" from a fresh Claude Code session today returns nothing useful unless the user names a file.

This work closes the gap with one front door, instead of N pointers to memorize.

### Goals (priority order)

1. **One query endpoint for all of Ryan's memory.** A single `POST /api/memory/query` (or `flyn-mem query`) returns ranked hits from every applicable source with citations.
2. **Cross-agent reach.** Flyn, Claude Code from any cwd, cron jobs, and arbitrary scripts can all consume the same surface.
3. **Honest sources.** Each hit carries an explicit `source` tag so the calling agent can cite. No silent answer synthesis.
4. **Drift visible, not auto-resolved.** When sources disagree, the calling agent sees both. A `lint` endpoint surfaces systemic drift on demand.
5. **No new daemon to operate.** Extends the existing MemoryRouter; no new launchd unit, no new port, no new logs directory.

### Non-goals

- **No LLM in the router.** The router never generates text or synthesizes answers. The calling agent does its own reasoning. (Non-LLM callers that want a synthesized answer use a follow-up `claude -p` call.)
- **No new ingest paths.** Phase 0's ingest API is unchanged. This work is read-only on the surface; only adapters change internally.
- **No federation across machines.** Localhost-only. Multi-machine sync is a future concern.
- **No caching.** Stale cache > 300 ms fan-out. Reconsider only if measurement shows otherwise.
- **No multi-owner scoping in this phase.** Single-owner (Ryan). Multi-owner is a future concern; existing conversation-memory spec already designs for it.

### Success criteria

A query is considered correctly served when:

1. `flyn-mem query "..."` returns within p95 < 500 ms for the default 8-source set (excluding `lossless` and `ocw_mem`).
2. Every hit has a `source`, `text`, `score` (post-RRF), and source-typed `metadata` populated.
3. Failed adapters are reported in `source_errors[]` with `query_id` correlation; the response is still 200 if at least one adapter succeeded.
4. Every query writes one JSONL line to `~/.flyn/memory-router/logs/query-YYYY-MM-DD.jsonl` with full per-source breakdown.
5. `flyn-mem health` confirms: CLI on PATH, service reachable, all adapters report status, auto-memory file present, workspace pointers present.

### Commitments inherited from existing spec

- **REST + curl from exec only.** No MCP for either side. (Postmortem 2026-04-21.)
- **launchd-managed long-running service.** Same `ai.flyn.memory-router.plist` Phase 0 ships.
- **File size discipline.** Soft cap 400 lines, hard cap 800 lines per file. This work targets ≤200 lines per new adapter, ≤250 for `query.py`, ≤300 for the extended `server.py`.
- **TDD per task.** Failing test → implementation → green → commit. No exceptions.

---

## Section 2 — Architecture

One service at `localhost:8400` with two surfaces:

```
                              ┌──────────────────────────┐
   Flyn (curl from exec)  ──► │                          │
   Claude Code (Bash)     ──► │   flyn-mem  CLI          │
   cron / scripts         ──► │   (Python entry point)   │
                              └────────────┬─────────────┘
                                           │ POST :8400/api/memory/query
                                           ▼
                          ┌────────────────────────────────┐
                          │  MemoryRouter  :8400           │
                          │  ─ POST /api/memory/ingest     │  (Phase 0, unchanged)
                          │  ─ POST /api/memory/pin        │  (Phase 0, unchanged)
                          │  ─ POST /api/memory/query  NEW │
                          │  ─ POST /api/memory/lint   NEW │
                          │  ─ GET  /api/memory/sources NEW│
                          │  ─ GET  /api/health            │
                          └────────────┬───────────────────┘
                                       │ asyncio.gather(*read_adapters)
        ┌──────────────────────────────┼───────────────────────────────────┐
        ▼              ▼               ▼              ▼              ▼
   hot_read       warm_read      reference_read    user_read    ol_wiki_read
   MEMORY.md +    Graphiti       ~/AI/openclaw/    ~/.claude/   :8200 REST
   pins           :8100          reference/wiki/   .../memory/
                  ws/memory/
        ▼              ▼               ▼              ▼              ▼
   cool_read       cold_read       lesson_read     ocw_mem_read  lossless_read
   daily          captures        KNOWLEDGE/       openclaw      Lossless
   roll-ups       index           append           memory        plugin
                                                   search CLI    (heavy)
```

**Three-tier internal architecture** (same pattern Phase 0 uses):

- **server.py** — FastAPI routes, request validation, response shaping.
- **query.py** — orchestration. Fan-out, gather, dedup, RRF merge, error collection.
- **adapters/*_read.py** — one per source. Pure read logic, no orchestration.

Each layer has a Protocol-defined seam. New sources = add one adapter file + one registry line. No `server.py` or `query.py` changes.

---

## Section 3 — Components

### server.py (extend)

Adds three routes on top of Phase 0's ~200 lines. Target file size: ≤300 lines total.

```
POST /api/memory/query    body: { q, include?, exclude?, top_k? }
                          200:  { query_id, hits, source_errors, elapsed_ms }
                          502:  all sources failed
                          400:  validation error

POST /api/memory/lint     body: { entities?, sources? }
                          200:  { drift_report: [...] }

GET  /api/memory/sources  200:  [ { name, kind, default_included, last_query_ts,
                                    last_error_ts, error_rate_100q } ]
```

### query.py (new)

The orchestrator. Target ≤250 lines.

```python
async def query(q: str,
                include: set[str] | None,
                exclude: set[str] | None,
                top_k: int = 10) -> QueryResult:
    sources = registry.filtered(include, exclude)
    raw = await asyncio.gather(
        *[asyncio.wait_for(s.query(q, top_k=top_k), timeout=s.read_timeout)
          for s in sources],
        return_exceptions=True,
    )
    hits_per_source, errors = _split_results(sources, raw)
    deduped = _dedup_by_canonical_id_and_text_hash(hits_per_source)
    return _rrf_merge(deduped, top_k=top_k, errors=errors)
```

### adapters/base.py (extend)

Adds `ReadAdapter` alongside the renamed `WriteAdapter`. Both keep the existing Protocol pattern.

```python
class ReadAdapter(Protocol):
    name: str                                                # "hot", "warm", ...
    read_timeout: float                                      # 2.0 default
    default_included: bool                                   # False for lossless, ocw_mem
    async def query(self, q: str, top_k: int = 10) -> list[Hit]: ...


class Hit(BaseModel):
    text: str
    source: str                                              # "warm/graphiti", ...
    score: float                                             # native; only used intra-source
    metadata: dict[str, Any]                                 # source-typed
```

### Ten read adapters (each ≤200 lines)

| Adapter | Strategy | Backend dep | Default? |
|---|---|---|---|
| `hot_read` | grep MEMORY.md sections + pin file | fs | yes |
| `warm_read` | GET `:8100/api/search?q=...` + ranked workspace/memory/*.md grep | httpx + fs | yes |
| `cool_read` | grep over daily roll-up files | fs | yes |
| `cold_read` | line-grep captures index | fs | yes |
| `lesson_read` | grep over `KNOWLEDGE/*.md` | fs | yes |
| `reference_read` | read `wiki/index.md` first, then walk `[[wikilinks]]` per vault CLAUDE.md schema | fs | yes |
| `user_read` | grep `~/.claude/projects/-Users-4c-AI/memory/` with frontmatter awareness | fs | yes |
| `ol_wiki_read` | GET `:8200/search?q=...` with PIN header | httpx | yes |
| `ocw_mem_read` | shell out: `openclaw memory search --query "..." --json` | subprocess | **no** |
| `lossless_read` | read plugin's on-disk session logs (heaviest) | fs | **no** |

### bin/flyn-mem (new)

Python entry-point installed via `pyproject.toml` console-scripts. Symlinked from `/usr/local/bin/flyn-mem` for PATH discoverability by every shell.

```
flyn-mem query "<question>"           [--include …] [--exclude …] [--top N] [--json]
flyn-mem ingest <event-json>          # forwards to /api/memory/ingest
flyn-mem sources                      # health + last-query timing per adapter
flyn-mem health                       # full system check (CLI, service, adapters, pointers)
flyn-mem logs                         # tail today's query log
flyn-mem logs --query-id <id>         # full trace, joins query + source-error logs
flyn-mem logs --grep "<text>"
flyn-mem logs --errors
flyn-mem logs --since <duration>
flyn-mem logs --source <name>
```

### config.py (extend)

Add `READ_SOURCES` registry. Python module, not YAML — same pattern Phase 0 follows.

```python
READ_SOURCES: dict[str, ReadSourceConfig] = {
    "hot":       ReadSourceConfig(cls=HotRead,      timeout=1.0, default=True),
    "warm":      ReadSourceConfig(cls=WarmRead,     timeout=2.0, default=True),
    "cool":      ReadSourceConfig(cls=CoolRead,     timeout=1.0, default=True),
    "cold":      ReadSourceConfig(cls=ColdRead,     timeout=1.0, default=True),
    "lesson":    ReadSourceConfig(cls=LessonRead,   timeout=1.0, default=True),
    "reference": ReadSourceConfig(cls=ReferenceRead, timeout=1.5, default=True),
    "user":      ReadSourceConfig(cls=UserRead,     timeout=1.0, default=True),
    "ol_wiki":   ReadSourceConfig(cls=OLWikiRead,   timeout=2.0, default=True),
    "ocw_mem":   ReadSourceConfig(cls=OCWMemRead,   timeout=3.0, default=False),
    "lossless":  ReadSourceConfig(cls=LosslessRead, timeout=3.0, default=False),
}
```

---

## Section 4 — Data flow

```
t=0ms   flyn-mem query "who is Beth?" --top 10
                         │
t=0ms   POST :8400/api/memory/query
                         │
t=1ms   server.py validates → query.py orchestrator
                         │
                         ▼ asyncio.gather(*8 default-included adapters)
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   hot_read (5ms)   warm_read (80ms)   ol_wiki_read (250ms)   ...
        │                │                │
        └────────────────┴────────────────┘
                         │ slowest wins (or its 2s timeout)
                         ▼
t≈250ms query.py: dedup (canonical_id + text-hash), then RRF merge
                         │
t≈255ms 200 OK { query_id, hits[…], source_errors[], elapsed_ms }
```

**RRF (reciprocal rank fusion):** for a hit at rank `r` in source `s`, contribute `1 / (k + r)` to its score (`k=60` by convention). Sum across sources. A hit found in 3 sources at ranks 5, 7, 9 outranks a hit at rank 1 in only one source — corroboration emerges naturally.

**Dedup precedes RRF.** Two collapse rules:
1. `metadata.canonical_id` match → same hit, sources listed together.
2. SHA-256 of normalized text (lowercase, collapsed whitespace) match → same hit.

Conservative — false negatives (missed dedup) are fine, false positives (collapsing real differences) are not.

**Performance budget:**
- p95 target: < 500 ms for the default 8-source set (most sources sub-100 ms; one external HTTP cap)
- Hard cap: 2.5 s — `asyncio.wait_for` wraps the whole gather
- No caching; no rate-limit beyond Phase 0's slowapi default (60/min/IP)

---

## Section 5 — Error handling, logging, observability

### Failure modes

| Failure | Behavior | Caller sees |
|---|---|---|
| Adapter timeout | Caught | `source_errors: [{source, error_class: "timeout"}]`, 200 |
| Adapter exception | Caught | Same, with `error_class` populated |
| Malformed `Hit` | Pydantic raises, source skipped | Same |
| All adapters fail | Nothing to merge | 502 + full error list, never silent |
| Empty result | Normal | 200 `{ hits: [] }` |
| Router unreachable | Connection error | CLI prints `launchctl print …` recovery hint |
| 5xx burst | CLI retries 1× with backoff, then surfaces | No silent retry storms |

### Logging contract

- `~/.flyn/memory-router/logs/query-YYYY-MM-DD.jsonl` — one JSON line per query: `query_id`, `ts`, `q`, `caller`, `included_sources`, per-source `{hits, elapsed_ms, error?}`, `total_elapsed_ms`, `top_k`.
- `~/.flyn/memory-router/logs/source-errors-YYYY-MM-DD.jsonl` — full stack traces + `query_id` correlation when adapters throw.
- `~/.flyn/memory-router/logs/ingest-YYYY-MM-DD.jsonl` — Phase 0 already, same shape. Cross-file `query_id` lets you reconstruct any query end-to-end.
- Retention: 90 days; older files gzipped; hard cap 1 GB; oldest gzips evicted if exceeded. Tunable via env (default 90 d / 1 GB).

### Drift detection — `POST /api/memory/lint`

For each entity surface found in `reference/wiki/index.md` (or supplied via `entities[]`), query all sources for that entity name. If pairwise text similarity < 0.6 between sources, flag.

```json
{
  "entity": "Beth",
  "sources": {
    "warm/graphiti": "...",
    "hot/MEMORY.md": "...",
    "reference/wiki/people/beth.md": "..."
  },
  "divergence": "graphiti missing 'COO Cora' attribute",
  "suggested_fix": "update Graphiti episode 'beth-intro-2026-04'"
}
```

Reported, not auto-resolved. Resolution remains Ryan's call.

### CLI lookup ergonomics

`flyn-mem logs` subcommands listed in Section 3 cover the everyday debugging cases. Any unexpected result → one `query_id` reconstructs the entire fan-out from logs.

### Health visibility

`GET /api/memory/sources` returns each adapter's last-query timing + 100-query rolling error rate. Lets you spot a degraded source without grep.

No Prometheus, no metrics endpoint — overkill at personal-machine scale.

---

## Section 6 — Testing strategy

Test layout matches Phase 0's existing structure under `deploy/memory-router/tests/`.

```
tests/
├── unit/
│   ├── test_query.py              # RRF math, dedup logic, source filtering
│   ├── test_read_adapters.py      # one class per adapter with fixtures
│   ├── test_cli.py                # flyn-mem flag parsing + subcommand dispatch
│   ├── test_logging.py            # JSONL shape, rotation, correlation IDs
│   └── test_lint.py               # drift detection
├── integration/
│   ├── test_query_roundtrip.py    # real app + fake adapters, POST cycle
│   ├── test_timeout_handling.py   # one slow adapter, others succeed
│   ├── test_partial_failure.py    # 5xx from one source → 200 + source_errors
│   └── test_cli_to_server.py      # CLI → real local server → response
└── fixtures/
    ├── reference_vault/           # mini Karpathy vault for reference_read tests
    ├── auto_memory/               # mini auto-memory dir for user_read tests
    └── mock_graphiti_response.json
```

**Discipline (inherited from Phase 0):**
- TDD per task: failing test → implement → green → commit.
- One real-server smoke test at end: launchd up, hit `/api/memory/query` with known fixture, assert response shape.
- After install, the install script runs `flyn-mem health` to verify the full system works end-to-end and reports any degraded sources.

**Coverage targets:**
- ≥90% line coverage on `query.py`, adapters, CLI
- No coverage requirement on `server.py` (route wiring) — integration tests cover behavior

---

## Section 7 — Cross-agent discovery

The reason this whole project exists. Four pieces.

### 1. CLI placement

`flyn-mem` installed two ways (defense in depth):
- `pip install -e .` registers as console-script in `~/.flyn/memory-router/.venv/bin/flyn-mem`
- Install script symlinks `/usr/local/bin/flyn-mem` → above. Every shell sees it.
- Install smoke test: `which flyn-mem` + `flyn-mem --version` + `flyn-mem health`.

### 2. Auto-memory entry — the glue for Claude Code

A new memory file at `~/.claude/projects/-Users-4c-AI/memory/feedback_memory_router.md`:

```markdown
---
name: memory-router-front-door
description: Cross-system memory queries on this Mac route through `flyn-mem` CLI (or POST :8400/api/memory/query). Spans Flyn workspace, Graphiti, OpenClaw memory, Karpathy vault, auto-memory, ol-wiki.
metadata:
  type: reference
---

For any "what does Ryan know about X" question, prefer `flyn-mem query "X"` before
filesystem grep or per-source reads. Returns ranked hits + citations across 10 sources.

Quick examples:
  flyn-mem query "who is Beth?"                  # all sources, top 10
  flyn-mem query "Flyn memory schema" --include reference,lesson
  flyn-mem query "..." --exclude lossless,ocw_mem
  flyn-mem sources                                # per-adapter health
  flyn-mem logs --query-id <id>                   # debug a result

Service runs at localhost:8400 (launchd: ai.flyn.memory-router).
If `flyn-mem` is missing: see ~/AI/openclaw/flyn-agent/deploy/memory-router/README.md
```

Plus one index line in `MEMORY.md`.

### 3. Per-agent pointer edits (5-line additions each)

- `flyn-agent/workspace/TOOLS.md` — add `flyn-mem` to the curl-tools table next to `:8100` and `:8200`.
- `flyn-agent/workspace/AGENTS.md` — add routing rule under "Rules of engagement": *"Before grepping workspace files or hitting Graphiti directly, try `flyn-mem query` first."*
- `~/AI/openclaw/reference/CLAUDE.md` — add "for queries outside this vault, use `flyn-mem query`."

### 4. Walkthrough

*Scenario: cwd is `~/somewhere-random/`, user asks "what's the latest on the OpenLit Linear sync?"*

```
1. Claude Code session loads auto-memory (includes the new memory-router-front-door entry).
2. Claude sees the question; auto-memory directs to flyn-mem.
3. Claude runs:    flyn-mem query "OpenLit Linear sync status" --top 5
4. flyn-mem POSTs :8400/api/memory/query — 8 default sources fan out.
5. Top hits return RRF-merged:
   - hot/MEMORY.md       "73/124 questions synced; remaining 51 blocked by Linear free-tier cap"
   - warm/graphiti        episode: linear-sync-2026-05-13
   - reference/wiki       page: openlit/linear-integration.md
   - ol_wiki              answered question I.42 about Linear plan
6. Claude synthesizes from those citations in its own turn.
   Total time: ~100 ms router + Claude's turn.
```

---

## Section 8 — Integration with existing Phase 0 (Path A)

This work folds into the existing `2026-05-15-flyn-memory-router-phase-0.md` plan. Same Phase 0 ships both ingest and query.

### Tasks added to the existing plan

The existing plan has Tasks 1–24 (ingest path through end-to-end ship gate). This work adds Tasks 25–42, in the same TDD-per-task format:

```
25. ReadAdapter Protocol + Hit model (extend adapters/base.py + types.py)
26. ReadSourceConfig + READ_SOURCES registry (extend config.py)
27. RRF merge + dedup logic (new query.py — pure functions first, no I/O)
28. hot_read adapter + tests
29. warm_read adapter + tests
30. cool_read + cold_read + lesson_read adapters + tests (similar; bundle)
31. reference_read adapter + tests (index-first walk per vault schema)
32. user_read adapter + tests (frontmatter-aware grep)
33. ol_wiki_read adapter + tests
34. ocw_mem_read + lossless_read adapters + tests (default-excluded)
35. /api/memory/query route + integration tests
36. /api/memory/lint route + drift detection tests
37. /api/memory/sources route + adapter health tracker
38. flyn-mem CLI (Python entry point) + subcommand tests
39. Logging contract enforcement (JSONL writers, rotation, retention)
40. Install script extensions (symlink + auto-memory write + pointer edits)
41. Live smoke test (flyn-mem health against running service)
42. MEMORY-ROUTER-PHASE-0-RUBRIC.md (success criteria machine-readable for outcomes_runner)
```

Each task follows the existing pattern: failing test → implement → green → commit.

### Files touched outside `deploy/memory-router/`

- `flyn-agent/workspace/TOOLS.md` — append flyn-mem section
- `flyn-agent/workspace/AGENTS.md` — append routing rule
- `~/.claude/projects/-Users-4c-AI/memory/feedback_memory_router.md` — new (created by install script)
- `~/.claude/projects/-Users-4c-AI/memory/MEMORY.md` — append one index line
- `~/AI/openclaw/reference/CLAUDE.md` — append cross-vault query pointer
- `flyn-agent/deploy/outcomes/MEMORY-ROUTER-PHASE-0-RUBRIC.md` — new rubric for outcomes_runner

---

## Section 9 — Execution model (local autonomous stack)

This project will be built autonomously using existing local tooling. **No Anthropic Managed Agents** (mismatch: 20-iteration cap, requires API key, runs in hosted container with no access to localhost:8100/8200, no incremental commits).

### The stack

```
1. Spec (this document)
       │ approved by Ryan
       ▼
2. Plan (existing 2026-05-15-flyn-memory-router-phase-0.md, extended via writing-plans skill)
       │ committed
       ▼
3. Rubric (deploy/outcomes/MEMORY-ROUTER-PHASE-0-RUBRIC.md, new)
       │
       ▼
4. outcomes_runner.py (existing) — grades each phase against rubric
       │
       ▼
5. subagent-driven-development skill (existing) — picks next unchecked task,
       │   dispatches a subagent to implement it TDD-style, verifies pass,
       │   commits, marks done, loops.
       ▼
6. /loop skill (optional) — auto-paces re-runs at session boundaries
```

### Why this fits, not Managed Agents

- **No iteration cap.** Subagent loop runs until plan checkboxes are exhausted.
- **OAuth subscription path.** Uses `claude -p` (Claude Code headless) — Ryan's Pro plan. $0 marginal cost.
- **Full repo write.** Subagents work in `flyn-agent/` directly; commits land in git as they go.
- **In-situ testing.** Adapters call real local `:8100` / `:8200`; tests can hit real services.
- **Granular checkpoints.** Each task is one commit. Failures localize. Rollback is `git revert`.

### How autonomy actually works

For each task in the plan:
1. Subagent reads the task spec (file structure, target, failing-test code).
2. Writes the failing test file.
3. Runs the test, confirms FAIL.
4. Implements the production code.
5. Re-runs the test, confirms PASS.
6. Runs the full test suite, confirms no regressions.
7. Commits with the task's standard message format.
8. Marks the task checkbox done in the plan.
9. Returns to the main session; main session picks next task.

For each phase (groups of tasks):
1. `outcomes_runner.py` runs `MEMORY-ROUTER-PHASE-0-RUBRIC.md` against the current state.
2. Grader: `satisfied` → continue to next phase; `needs_revision` → return gaps to the main session, which queues fix tasks.
3. After all phases satisfied: run `flyn-mem health` as final acceptance check. Ship.

### Failure recovery

- Subagent test fails after implementation → it loops in TDD (test → fix → test) until green or it hits a complexity cap (3 attempts), then escalates with a summary to the main session.
- Subagent finds spec ambiguity → halts, returns clarifying question. Main session resolves (interactive or by re-reading the spec); then re-dispatches.
- A whole task is malformed → main session updates the plan, re-runs.
- All retries exhausted → session pauses with a written checkpoint. Ryan resumes after triage.

---

## Section 10 — Open questions and deferred decisions

These are intentionally not decided here. They become relevant only after Phase 0 ships.

1. **Multi-owner read scoping.** When Beth or Eric queries via Flyn, do they see Ryan's `user_read` source? Phase 0 is single-owner; multi-owner is a future concern aligned with the existing `conversation-memory` spec design.
2. **Streaming results.** Server-Sent Events for "hits as they arrive" — defer unless measured latency calls for it.
3. **Adaptive default source set.** Right now `lossless` and `ocw_mem` are default-excluded statically. Could be query-pattern-aware in future ("if q matches a session timeframe, include lossless").
4. **Cross-machine federation.** When Flyn runs on more than one host, query needs a topology layer. Out of scope.
5. **Auto-resolve drift.** Currently lint reports, Ryan resolves. Could promote to "router writes a reconcile ingest event" — defer until drift volume warrants it.
6. **Rubric coverage for `lint` quality.** No criterion yet measures the false-positive rate of the drift detector. Add once we have real lint runs to calibrate.

---

## Section 11 — References

- Andrej Karpathy LLM Wiki gist: `karpathy/442a6bf555914893e9891c11519de94f`
- kepano/obsidian-skills: <https://github.com/kepano/obsidian-skills>
- Anthropic Managed Agents `define_outcome` docs: <https://platform.claude.com/docs/en/managed-agents/define-outcomes> (researched; not used for this project — see Section 9 rationale)
- POSTMORTEM-2026-04-21 (no-MCP rule): `~/AI/openclaw/flyn-agent/POSTMORTEM-2026-04-21.md`
- Existing orchestrator spec: `~/AI/openclaw/flyn-agent/docs/superpowers/specs/2026-05-15-flyn-orchestrator-design.md` (§2.5 — original MemoryRouter, write-only)
- Existing Phase 0 plan: `~/AI/openclaw/flyn-agent/docs/superpowers/plans/2026-05-15-flyn-memory-router-phase-0.md` (Tasks 1–24; this spec adds Tasks 25–42)
- New reference vault: `~/AI/openclaw/reference/CLAUDE.md` (Karpathy LLM Wiki schema)
- Reciprocal Rank Fusion: Cormack, Clarke, Buettcher (2009), "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods"

---

## Spec self-review notes (this section deleted after user approval)

**Placeholder scan:** searched for TBD / TODO / XXX / FIXME / ??? / VERIFY — clean. Section 10 explicitly labels deferred decisions as deferred, not as placeholders.

**Internal consistency:** Section 2 (architecture) matches Section 3 (components) matches Section 4 (data flow). Section 8 (Path A) matches the user's explicit selection. Section 9 (execution model) matches the user's choice of local autonomous stack.

**Scope check:** This is one cohesive spec for one phase of one service. Folds into an existing plan rather than spawning new ones. Within scope for `writing-plans` next.

**Ambiguity check:** Two intentional ambiguities, both flagged in Section 10: multi-owner and streaming. Everything else resolved.

**Boundary check:** the spec specifies what to build, not how each adapter parses its source format. Adapter internals are TDD-discovered during implementation, not pre-specified — same discipline Phase 0 uses.
