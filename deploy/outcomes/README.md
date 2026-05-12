# Outcomes Runner (v0)

Rubric-driven iteration loop. Reads a phase rubric (e.g.
`flyn-agent/deploy/wiki-backend/PHASE-RUBRICS.md`), runs a worker→grader
loop on unmet criteria until the grader passes everything or max_iter is hit.

## Status

**v0 scaffold.** Uses the standard Anthropic Messages API with a worker+grader
pattern. The official Managed Agents **Outcomes** endpoint (public beta
2026-05-06) is the cleaner production path; this scaffold demonstrates the
loop shape and is ready to swap to the official endpoint when wider beta opens.

## Use

```bash
export ANTHROPIC_API_KEY=<key>   # or stored in ~/.openclaw/agents/main/agent/auth-profiles.json
./outcomes_runner.py \
  --rubric /Users/4c/AI/flyn-agent/deploy/wiki-backend/PHASE-RUBRICS.md \
  --phase 2 \
  --max-iter 5
```

Logs land at `~/.openclaw/logs/outcomes/<run-id>-phase<N>.json`.

## What it does NOT do (yet)

- Doesn't actually execute the worker's proposed commands (it just plans + reports). Future iteration: shell-tool integration so worker can write files / run tests directly.
- Doesn't use the official Outcomes API endpoint (uses Messages API instead).
- Doesn't have parallel iteration (sequential one phase at a time).

## Wiring into Flyn

The next step is registering this as a Flyn skill so Ryan can DM Flyn:

> Run outcomes on Phase 4

And Flyn shells out to `outcomes_runner.py --rubric ... --phase 4`, then DMs the report.
