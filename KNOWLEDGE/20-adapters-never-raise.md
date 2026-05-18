---
name: adapters-never-raise
description: Channel/PM/Notify adapter methods must never propagate exceptions. HTTP/IO failures stub-return or no-op. The contract conformance suite enforces this — a single uncaught exception from an adapter brings the orchestrator down.
type: reference
---

# Adapter methods never raise

Pattern formalized in Phase 7 PMAdapter conformance suite (`test_pm_adapter_conformance.py`), but applies to every adapter Protocol the orchestrator imports.

## The contract

```python
@runtime_checkable
class PMAdapter(Protocol):
    name: str
    def create_task(self, t: TaskRecord) -> str: ...          # returns external_id
    def update_state(self, t: TaskRecord, to_state: TaskState) -> None: ...
    def link_artifact(self, t: TaskRecord, artifact: dict) -> None: ...
    def comment_on_task(self, t: TaskRecord, body: str) -> None: ...
```

None of these methods are allowed to raise. If the underlying HTTP/IO/auth fails:

- `create_task`: return a synthetic external_id (`f"{name}-stub-{t.task_id}"`) so the orchestrator has SOMETHING to record. The mirror to the external system is best-effort.
- The three void methods: silently no-op.

## Why

The orchestrator calls adapters from inside the state-machine spine. If an adapter raises, it propagates up through `run_task`, hits the catch-all `except Exception` block, and the task transitions to `FAILED`. From the operator's perspective: "I sent a task and Flyn told me it failed" — but the real work (worker dispatched, builder produced a draft, reviewer approved) all happened successfully. The failure was just the side-channel mirror to Linear or OL Wiki, which the operator doesn't care about as a primary outcome.

A noisy adapter creates false-positive task failures. A silent adapter creates the right user experience: the task ships its primary deliverable, and the side-channel mirror is opportunistic.

## Implementation pattern

```python
def create_task(self, t: TaskRecord) -> str:
    try:
        resp = self._http(method="POST", url=f"{self._base_url}/api/...", json=payload, timeout=5)
        data = resp.json()
        external_id = data.get("id")
        if external_id is None:
            return f"{self.name}-stub-{t.task_id}"
        return f"{self.name}-{external_id}"
    except Exception:
        return f"{self.name}-stub-{t.task_id}"
```

The `Exception` catch is broad-on-purpose. Adapters are at the IO boundary; we don't want network errors, timeout errors, JSON parse errors, or auth errors leaking. The cost of swallowing a real bug is low (logs will show it; downstream the task still works) compared to the cost of cascading a side-channel failure into a task failure.

## What about telemetry

Adapters that perform real I/O accept an optional `memory_emitter` kwarg. When wired, swallowed errors fire `adapter_swallowed_error` memory events via the shared `emit_swallowed_error` helper at `adapters/_observability.py`. The helper is itself wrapped in try/except — a broken memory emitter cannot violate the adapter's never-raise contract.

```python
# In the adapter:
def create_task(self, t: TaskRecord) -> str:
    try:
        resp = self._http(method="POST", url=..., json=..., timeout=5)
        return f"{self.name}-{resp.json()['id']}"
    except Exception as e:
        emit_swallowed_error(self._memory_emitter, self.name, "create_task", e, task_id=t.task_id)
        return f"{self.name}-stub-{t.task_id}"
```

The helper emits with this shape:
- `event_type="adapter_swallowed_error"`
- `subject=task_id` (or adapter name when no task_id available)
- `body=f"{adapter_name}.{method} swallowed {ExceptionClass}: {truncated_message}"`
- `dedup_key=f"adapter-{adapter_name}-{method}-{task_id_or_'no-task'}"`
- `importance="cool"`

Wired in 4 adapters via PR #24 (§Δ.adapter-observability): OLWikiPMAdapter, WebhookPMAdapter, TelegramChannelAdapter, EmailChannelAdapter. Pass `memory_emitter=MemoryEmitter(...)` at adapter construction. When omitted (default), the adapter behaves exactly as before — swallowed errors are silent.

## Conformance enforcement

The conformance suite tests:

```python
def test_adapter_swallows_http_failure(pm_adapter_with_failing_http):
    name, a = pm_adapter_with_failing_http
    # Adapter's http injected to always raise. Every method must return cleanly.
    result = a.create_task(stub_task)
    assert isinstance(result, str)
    assert result   # non-empty
    a.update_state(stub_task, TaskState.RUNNING)   # must not raise
    a.link_artifact(stub_task, {})                 # must not raise
    a.comment_on_task(stub_task, "x")              # must not raise
```

This runs against every PMAdapter implementation via `pytest.mark.parametrize`. A new adapter that raises gets caught at PR time, not at 3am when a worker hits a transient Linear API error.
