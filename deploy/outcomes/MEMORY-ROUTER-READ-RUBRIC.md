# Memory-Router Read-Side Rubric (Phase 0c-0f)

Machine-gradable success criteria for the read-side extension. Run via
`outcomes_runner.py --rubric deploy/outcomes/MEMORY-ROUTER-READ-RUBRIC.md`.

## Types & adapter contracts

- [ ] `flyn_memory_router.types.Hit` exists with fields text, source, score, metadata
- [ ] `flyn_memory_router.types.QueryResult` exists with query_id, hits, source_errors, elapsed_ms
- [ ] `flyn_memory_router.types.LintFinding` and `LintReport` exist
- [ ] `flyn_memory_router.adapters.base.ReadAdapter` Protocol with async query
- [ ] `flyn_memory_router.config.READ_SOURCES` registers all 10 expected adapters
- [ ] ocw_mem and lossless are default_included=False

## Adapters built

- [ ] adapters/hot_read.py:HotRead exists and tests pass
- [ ] adapters/warm_read.py:WarmRead exists and tests pass
- [ ] adapters/cool_read.py:CoolRead exists and tests pass
- [ ] adapters/cold_read.py:ColdRead exists and tests pass
- [ ] adapters/lesson_read.py:LessonRead exists and tests pass
- [ ] adapters/reference_read.py:ReferenceRead exists and tests pass
- [ ] adapters/user_read.py:UserRead exists and tests pass
- [ ] adapters/ol_wiki_read.py:OLWikiRead exists and tests pass
- [ ] adapters/ocw_mem_read.py:OCWMemRead exists and tests pass
- [ ] adapters/lossless_read.py:LosslessRead exists and tests pass

## Orchestrator & routes

- [ ] query.rrf_merge(per_source, top_k) — RRF_K==60, canonical_id dedup, text-hash dedup
- [ ] async query.query(q, include, exclude, top_k) exists
- [ ] POST /api/memory/query route — integration tests pass
- [ ] POST /api/memory/lint route — drift tests pass
- [ ] GET /api/memory/sources route — returns name, default_included, last_elapsed_ms, error_rate_100q
- [ ] health_tracker.HealthTracker records timeouts/exceptions/success per source

## CLI

- [ ] flyn-mem console-script registered in pyproject.toml
- [ ] flyn-mem query "<q>" prints hits when service reachable
- [ ] flyn-mem query non-zero with launchctl hint when unreachable
- [ ] flyn-mem health prints overall + per-source state
- [ ] flyn-mem sources prints JSON
- [ ] flyn-mem logs --query-id <id> joins query + source-errors logs

## Logging

- [ ] Each query writes ~/.flyn/memory-router/logs/query-YYYY-MM-DD.jsonl
- [ ] Each failure writes source-errors-YYYY-MM-DD.jsonl with matching query_id
- [ ] logging_contract.gc_logs() handles 90-day gzip + 1GB cap

## Install + discovery

- [ ] install.sh symlinks /usr/local/bin/flyn-mem (or prints sudo command)
- [ ] After install, ~/.claude/projects/-Users-4c-AI/memory/feedback_memory_router.md exists
- [ ] MEMORY.md has exactly one index line for feedback_memory_router.md (idempotent)
- [ ] workspace TOOLS.md has exactly one ## flyn-mem section (idempotent)

## Live smoke (manual; only graded with --smoke)

- [ ] /api/health returns {ok: true}
- [ ] /api/memory/query with q="Flyn" returns 200 with query_id
- [ ] today's query-*.jsonl contains that query_id
- [ ] flyn-mem health prints all 10 sources

## Soft commitments

- [ ] No new launchd unit added (one service)
- [ ] No new daemon, no new port
- [ ] All new file sizes meet caps: ≤200 per adapter, ≤250 query.py, ≤300 server.py
- [ ] All commits follow feat(memory-router): prefix
