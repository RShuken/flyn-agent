# flyn-orchestrator

Multi-channel dev-team-plus orchestrator on `http://localhost:8300`. Accepts tasks from Cora teammates (Ryan, Beth, Eric) via Telegram + future channels; dispatches headless `claude -p` or `codex exec` workers in git worktrees; runs fresh-context reviewers; mirrors task state to Linear + (future) Cora PM; reports back via the originating channel.

**Spec:** `../../docs/superpowers/specs/2026-05-15-flyn-orchestrator-design.md`
**Plan:** `../../docs/superpowers/plans/2026-05-15-flyn-orchestrator-phase-1-mvp.md`

## Public interface

- `POST /api/tasks/inbound` — accept a synthetic or channel-delivered task
- `POST /api/tasks/<id>/approve` — advance to next state at an approval gate
- `GET /api/health` — liveness
- `GET /api/tasks/<id>` — task detail

## How to add a worker backend

Drop `flyn_orchestrator/backends/<name>.py` implementing the `WorkerBackend` Protocol. Register in `backends/__init__.py`.

## How to add a channel/notify/PM adapter

Drop a file under `flyn_orchestrator/adapters/{channels,notify,pm}/<name>.py` implementing the matching Protocol from `adapters/base.py`. Register in the corresponding `__init__.py`.

## Common gotchas

- Don't bypass the Phase 0 memory router — all memory writes via `:8400/api/memory/ingest`.
- Workers are tool processes, not peer agents (per AGENTS.md rule).
- `claude -p` OAuth refresh can fail in long runs; `ANTHROPIC_API_KEY` is the documented fallback.
