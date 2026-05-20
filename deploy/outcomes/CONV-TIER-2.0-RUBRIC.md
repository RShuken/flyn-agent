# Conv-Tier 2.0 — Build It Right

Machine-gradable success criteria. Run via:

```
deploy/outcomes/outcomes_runner.py score \
  --rubric deploy/outcomes/CONV-TIER-2.0-RUBRIC.md \
  --checklist
```

## Design + SLOs

- [ ] docs/superpowers/specs/conv-tier-2.0-design.md committed
- [ ] SLOs: p50 e2e < 2s, p99 < 5s, drop rate < 0.1%
- [ ] State machine diagram + transitions documented
- [ ] Migration plan from v1 documented

## Workflow Schema

- [ ] conversation_workflow table: message_id, state, attempts, last_error, created_at, encrypted_at, summarized_at, promoted_at, completed_at
- [ ] State enum: received → encrypted → indexed → summarized → promoted → complete
- [ ] Idempotency_key column per stage
- [ ] Migration script preserves existing rows
- [ ] All state transitions atomic (single SQL statement)

## Async Pipeline

- [ ] ingest_worker writes row + emits encrypt event
- [ ] encrypt_worker calls AES-GCM, updates state
- [ ] summarize_worker calls Ollama, updates summary + state
- [ ] promote_worker calls Graphiti, updates state
- [ ] Each worker is independent asyncio coroutine
- [ ] Concurrency configurable per stage
- [ ] No daemon-thread polling anywhere
- [ ] await queue.get() pickup latency < 50ms

## Work Queue

- [ ] asyncio.Queue with SQLite-backed overflow
- [ ] Queue state persisted on enqueue
- [ ] Recovered on restart without loss
- [ ] HIGH_WATER threshold enforced (default 1000)
- [ ] Drop policy configurable (oldest/newest/reject_new)
- [ ] System overload signal emitted to controller

## Observability

- [ ] trace_id assigned at ingest, flows through every stage
- [ ] Structured logs: stage, message_id, trace_id, duration_ms, outcome
- [ ] GET /api/memory/conv/health returns queue_depths, p50_ms, p99_ms per stage, stuck_count, dead_letter_count, workers_alive
- [ ] GET /metrics in Prometheus format
- [ ] Single SQL query lists all stuck messages

## Idempotency + Retries

- [ ] Each external call tagged with idempotency_key
- [ ] Exponential backoff per stage (configurable max attempts)
- [ ] Dead-letter dir for exhausted retries
- [ ] Local dedup table for non-idempotent externals
- [ ] Replay-safe: re-running a stage doesn't corrupt state

## Reliability

- [ ] Worker pool supervised (auto-restart on death)
- [ ] Graceful shutdown drains queue (30s timeout)
- [ ] Crash recovery resumes from persisted state
- [ ] kill -9 chaos test verifies recovery
- [ ] No data loss under power-loss simulation

## Test Coverage

- [ ] State machine property-based tests (every state has defined next state)
- [ ] Each worker unit-testable in isolation with mocked externals
- [ ] Load test: 10x normal rate, verify backpressure activates
- [ ] Chaos test: kill worker mid-pipeline, full recovery
- [ ] End-to-end timing assertions (p99 < SLO)
- [ ] 95%+ code coverage on pipeline modules

## Deployment

- [ ] v2 runs alongside v1 in shadow mode for 24h
- [ ] Output sampling: v1 vs v2 outputs match
- [ ] Cut over with monitoring
- [ ] v1 code paths removed
- [ ] CHANGELOG entry committed
- [ ] PR merged to main

## Live Performance (manual; only graded with --live)

- [ ] p50 e2e latency < 2s over 10 sample messages
- [ ] p99 e2e latency < 5s
- [ ] Stuck count = 0 after 24h soak
- [ ] Worker memory bounded (no growth over 24h)
- [ ] No disk-space leak in overflow queue
- [ ] Dead-letter rate < 0.1% over 100 messages

## Stop

All non-manual criteria green. Live load + chaos passed. PR merged.
