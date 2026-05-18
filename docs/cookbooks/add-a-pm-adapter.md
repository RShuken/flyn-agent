# Cookbook: Add a new PMAdapter

A PMAdapter mirrors task lifecycle events to an external PM system. Linear, OL Wiki, and the generic Webhook adapter all conform to the same 4-method `PMAdapter` Protocol. This guide walks you through adding a new one.

After this guide you'll have an `<system>_pm.py` module under `flyn_orchestrator/adapters/pm/` that ships with conformance test coverage.

## When to add a PMAdapter

You're adding a PMAdapter when a new project-management or knowledge-tracking system needs to receive task-lifecycle mirrors. Concrete examples:

- **Jira** — common enterprise PM
- **Asana** — task management
- **Notion** — for teams that track work in a Notion database
- **Cora PM** — the future Cora team's internal PM (Phase 7.3-7.4)

If the target system is just a generic webhook receiver, **don't add a dedicated adapter** — use the existing `WebhookPMAdapter` and pass its URL.

## The contract

`flyn_orchestrator/adapters/base.py` defines:

```python
@runtime_checkable
class PMAdapter(Protocol):
    name: str
    def create_task(self, t: TaskRecord) -> str: ...                       # returns external_id
    def update_state(self, t: TaskRecord, to_state: TaskState) -> None: ...
    def link_artifact(self, t: TaskRecord, artifact: dict) -> None: ...
    def comment_on_task(self, t: TaskRecord, body: str) -> None: ...
```

**Three invariants:**

1. **`name` is a unique string.** It's used in audit logs and the `external_id` return value (e.g., `jira-PROJ-42`). Keep it short and kebab-or-snake-cased.

2. **Methods NEVER raise.** HTTP failures, auth failures, malformed responses — all swallowed. `create_task` returns a synthetic stub id (`f"{self.name}-stub-{t.task_id}"`); the three void methods silently no-op. The conformance suite enforces this; see `KNOWLEDGE/20-adapters-never-raise.md`.

3. **State mirroring is best-effort.** If the external system has no native state field (like OL Wiki), `update_state` is a documented no-op. The durable artifact is the `create_task` record.

## Build it — step by step

### 1. Adapter module

Create `deploy/orchestrator/flyn_orchestrator/adapters/pm/<system>.py`. Reference implementations:

- `linear.py` (52 lines) — stub-style; no real API calls in MVP
- `olwiki.py` — wraps `POST /api/decisions` on a local FastAPI server
- `webhook.py` — generic JSON POST with optional secret header

**Pattern (mirrors `olwiki.py`):**

```python
"""<System>PMAdapter — wraps <SYSTEM>'s REST API.

Configured via auth-profiles.json (slot `<system>:default`) or env var
<SYSTEM>_TOKEN. Stub-mode when neither is set.

Adapter best-effort guarantee: every method swallows HTTP/auth failures
and returns stub values. Never raises.
"""
from __future__ import annotations
from typing import Any, Callable, Optional

from ._http import default_http
from ...types import TaskRecord, TaskState


class <System>PMAdapter:
    name = "<system>"

    def __init__(
        self,
        base_url: str = "https://api.<system>.com",
        token: Optional[str] = None,
        http: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token or self._load_token()
        self._http = http or default_http

    def _load_token(self) -> Optional[str]:
        # auth-profiles fallback + env-var fallback. See linear.py for the pattern.
        ...

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def create_task(self, t: TaskRecord) -> str:
        if not self.configured:
            return f"{self.name}-stub-{t.task_id}"
        try:
            payload = {
                "title": (t.intent or "")[:300] or f"Flyn task {t.task_id}",
                "description": f"Workflow: {t.workflow}\nRequester: {t.sender_identifier}\n\n{t.intent}",
                # ... system-specific fields
            }
            headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
            resp = self._http(method="POST", url=f"{self._base_url}/...", json=payload, timeout=5, headers=headers)
            data = resp.json() if hasattr(resp, "json") else resp
            ext_id = data.get("id") if isinstance(data, dict) else None
            if ext_id is None:
                return f"{self.name}-stub-{t.task_id}"
            return f"{self.name}-{ext_id}"
        except Exception:
            return f"{self.name}-stub-{t.task_id}"

    def update_state(self, t: TaskRecord, to_state: TaskState) -> None:
        # If <system> has a state field: PATCH /tasks/{id} with the new status.
        # If not: no-op (and document why).
        return

    def link_artifact(self, t: TaskRecord, artifact: dict) -> None:
        # Attach a URL/file reference to the external task (e.g., PR URL after dev workflow ships).
        return

    def comment_on_task(self, t: TaskRecord, body: str) -> None:
        # POST a comment to the external task.
        return
```

### 2. Reuse the shared HTTP helper

Don't write your own urllib boilerplate. `_http.py` already provides `default_http(method, url, json, timeout, headers=None)` returning an object with `.json()` and `.status_code`. Pass `http=stub_callable` for tests.

### 3. Tests

Create two files:

**`tests/unit/test_<system>_adapter.py`** — system-specific tests:
- `create_task` POSTs the correct payload shape to the right URL
- Returns `<system>-<id>` on successful response with `id` field
- Returns `<system>-stub-<task_id>` when response has no `id`
- Returns `<system>-stub-<task_id>` when HTTP raises (timeout, ConnectionError, etc.)
- `update_state` / `link_artifact` / `comment_on_task` — assert the expected HTTP call shape, or assert no call if no-op

**`tests/unit/test_pm_adapter_conformance.py`** — add a parametrize entry for your adapter:

```python
@pytest.fixture(params=[
    pytest.param(("linear", lambda: LinearPMAdapter(api_key="stub")), id="linear"),
    pytest.param(("olwiki", lambda: OLWikiPMAdapter(http=_stub_http)), id="olwiki"),
    pytest.param(("webhook", lambda: WebhookPMAdapter(target_url="http://stub.local", http=_stub_http)), id="webhook"),
    pytest.param(("<system>", lambda: <System>PMAdapter(http=_stub_http, token="stub")), id="<system>"),  # ← your row
])
def pm_adapter(request):
    name, factory = request.param
    return name, factory()
```

The existing conformance tests will run against your adapter automatically:
- `test_adapter_implements_protocol` — `isinstance(adapter, PMAdapter)` under `@runtime_checkable`
- `test_adapter_has_name` — non-empty `name` attribute
- `test_create_task_returns_string` — `create_task` returns a non-empty string
- `test_void_methods_return_none` — `update_state` / `link_artifact` / `comment_on_task` return None
- `test_adapter_swallows_http_failure` — when HTTP raises, methods return cleanly (no exception)
- `test_configured_is_bool` — `configured` property returns a bool

Adding the parametrize row gives your adapter free coverage on all 6 contract guarantees.

### 4. Register in the channel registry (if you want auto-mirror)

If your adapter should be invoked automatically on every task lifecycle event, register it where the orchestrator constructs its PM mirror list. Currently (Phase 7 MVP) there is no automatic mirror loop — adapters are constructed and called manually by callers. Phase 7b adds a `PMRegistry` that fan-outs lifecycle events to all configured adapters; until then, instantiate your adapter where it's needed.

### 5. Ship checklist

- [ ] `adapters/pm/<system>.py` adapter module
- [ ] `tests/unit/test_<system>_adapter.py` system-specific tests
- [ ] `tests/unit/test_pm_adapter_conformance.py` parametrize row added
- [ ] All conformance tests pass (~6 free tests fire automatically)
- [ ] Token-loading documented (which env var / which auth-profiles slot)
- [ ] If a rubric criterion was waiting on this adapter: update Phase 7 row + score
- [ ] `audit/_baseline.md` §Δ subsection if any new pattern surfaced

## Anti-patterns to avoid

- **Raising from any adapter method.** The whole orchestrator's reliability depends on this guarantee. If you find yourself wanting to surface an HTTP error, emit a memory event instead and stub-return.
- **Synchronous-only HTTP.** Don't use the `requests` library (synchronous, adds a dep). Use the shared `_http.py` urllib helper. If you need async, that's a separate refactor of the whole adapter layer.
- **Hardcoded credentials.** Always load from auth-profiles or env. Never check secrets into the repo.
- **Per-event API calls without batching consideration.** If the external system rate-limits you, batch lifecycle events in your adapter (queue them; flush every N seconds). The orchestrator doesn't care if `comment_on_task` returns before the network call completes.
- **Direct import of `requests` / `httpx` / similar.** Stick with stdlib + injection pattern; tests will be much easier.

## See also

- `KNOWLEDGE/20-adapters-never-raise.md` — the best-effort guarantee in detail
- `KNOWLEDGE/18-cross-module-mock-patching.md` — how to import HTTP helpers so test patches still intercept
- `flyn_orchestrator/adapters/base.py` — the Protocol definition itself
- `flyn_orchestrator/adapters/pm/{linear,olwiki,webhook}.py` — reference implementations
