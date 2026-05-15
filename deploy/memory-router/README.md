# flyn-memory-router

Universal memory-ingestion router on `http://localhost:8400`. Accepts events from anywhere in the Flyn stack, classifies importance, fans out to memory tiers (hot/warm/cool/cold/lesson), deduplicates, queues with backpressure.

**Spec:** `../../docs/superpowers/specs/2026-05-15-flyn-orchestrator-design.md` §2.5
**Plan:** `../../docs/superpowers/plans/2026-05-15-flyn-memory-router-phase-0.md`

## Public interface

- `POST /api/memory/ingest` — write an event; idempotent by `(source, dedup_key)`
- `POST /api/memory/pin` — Owner-only, mark a hot-tier pin permanent
- `DELETE /api/memory/pin/<subject>` — Owner-only, unpin
- `GET /api/health` — liveness probe

## How to add a new memory tier or sub-target

Drop a new file under `flyn_memory_router/adapters/<name>.py` implementing the `MemoryAdapter` Protocol (`adapters/base.py`). Register it in `adapters/__init__.py`. Add a tier mapping in `router.py`. No changes to `server.py` or `router.py`'s core logic.

## Common gotchas

- The router does NOT write to Lossless Claw. Lossless covers conversation turns; orchestrator events flow only through the router.
- `dedup_key` is namespaced by `source` — `("telegram", "msg-123")` ≠ `("orchestrator", "msg-123")`.
- Cool/cold tiers bypass Gemini entirely. Warm+ uses Graphiti (Gemini embeddings).
- Hot-tier pins decay (24h post-completion / 72h active). Owner can mark permanent via `/api/memory/pin`.
