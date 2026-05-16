# Phase 2c: Router Refactor — Phase-Runner Modules

**Date:** 2026-05-16
**Status:** Approved-for-implementation
**Spec author:** Claude Opus 4.7 (1M context)
**Phase rubric reference:** `deploy/outcomes/ORCHESTRATOR-PHASE-RUBRIC.md` Phase 2c backlog

## Goal

Decompose `flyn_orchestrator/router.py` (currently 1,398 lines, approaching the 1,500-line god-file threshold) into the main `TaskRouter` dispatcher (~250 lines) plus four per-workflow phase-runner modules. The refactor preserves all existing behavior — every public method on `TaskRouter`, every state transition, every audit row, every memory event remains byte-identical.

**Net line change:** router.py loses ~725 lines (the four `_run_*_phase` methods + `_execute_ops_and_finalize` + `_handle_ops_approval` + the per-workflow branches inside `handle_approval`). Those lines move to four new modules totalling ~670 lines. The slight reduction comes from removing duplicated `self._memory.emit` boilerplate and the now-unneeded `self.` indirection.

## Architecture

### Before

```
flyn_orchestrator/
├── router.py (1,398 lines) — TaskRouter does EVERYTHING
│     - __init__, accept, run_task
│     - _run_dev_pr_phase
│     - _run_research_phase
│     - _run_content_phase
│     - _run_ops_phase + _execute_ops_and_finalize + _handle_ops_approval
│     - handle_approval (multi-workflow if/elif)
│     - _notify, _format_notify_body, _transition, _safe_transition, _render_builder_prompt, _compute_diff
├── research.py (helper functions: decompose_intent, run_researchers, …)
├── content.py (helper functions: spec_content, draft_content, …)
├── ops.py (helper functions: spec_ops_action, classify_risk, …)
└── ...
```

### After

```
flyn_orchestrator/
├── router.py (~250 lines) — thin dispatcher
│     - __init__, accept, run_task (delegates to phase runners)
│     - handle_approval (thin dispatcher to phase modules)
│     - _notify_originating_channel, _format_notify_body  ← stay (shared)
│     - _transition, _safe_transition                     ← stay (shared)
│     - _render_builder_prompt, _compute_diff             ← stay (used by main builder flow)
├── phase_services.py (~30 lines) — shared services dataclass
│     - PhaseServices(store, dispatcher, memory, channels, transition_fn, safe_transition_fn, notify_fn, ...)
├── dev_phase.py (~120 lines)
│     - run_pr_phase(task, plan_obj, findings, worktree_path, repo_path, services) -> TaskRecord
│     - handle_approval(task, decision, services) -> TaskRecord
├── research_phase.py (~120 lines)
│     - run(task, services) -> None
├── content_phase.py (~180 lines)
│     - run(task, services) -> None
│     - handle_approval(task, decision, services) -> TaskRecord
├── ops_phase.py (~250 lines)
│     - run(task, services, *, workflows_dir=None) -> None
│     - execute_and_finalize(task, services) -> None
│     - handle_approval(task, *, approver, decision, approver_role, rationale, services) -> TaskRecord
├── research.py     ← unchanged (pure LLM helpers)
├── content.py      ← unchanged (pure LLM helpers)
└── ops.py          ← unchanged (pure LLM helpers)
```

## Components

### `PhaseServices` dataclass

A lightweight bundle of references that phase-runner functions need. Eliminates threading 7-8 individual arguments through every signature.

```python
# flyn_orchestrator/phase_services.py
from dataclasses import dataclass
from typing import Callable, Optional
from .adapters import ChannelRegistry
from .memory import MemoryEmitter
from .reviewer import review as _default_review
from .state import StateStore
from .types import ReviewFindings, TaskRecord, TaskState

@dataclass(frozen=True)
class PhaseServices:
    store: StateStore
    memory: MemoryEmitter
    channels: Optional[ChannelRegistry]
    reviewer_invoker: Callable[..., ReviewFindings]
    transition: Callable[[str, TaskState, TaskState, str, str], None]
    safe_transition: Callable[[str, TaskState, TaskState, str, str], None]
    notify: Callable[..., None]  # _notify_originating_channel
```

`PhaseServices` is **immutable** (frozen dataclass). Phase runners don't mutate it — they call the bound methods/functions and pass results back via state updates on `services.store`.

Why dataclass rather than passing `TaskRouter` itself: avoids circular import, makes phase runners trivially unit-testable with mock services, and explicitly enumerates the surface area each phase needs from the router.

### Phase-runner module convention

Each phase module exports module-level functions (not classes). The contract:

- `run(task: TaskRecord, services: PhaseServices) -> None` — drives the workflow's state machine for the happy path. Mutates state via `services.store` and `services.transition`. Raises on unrecoverable error; the caller handles failure transitions.
- `handle_approval(...)` — optional. Present for workflows that pause for human approval (content, ops, dev). Returns the updated `TaskRecord`.
- Helpers private to that workflow (e.g., `_format_review_comment` for dev) stay module-private with leading underscore.

The current `_run_*_phase` methods are pure-ish: they call `self._store`, `self._memory.emit`, `self._transition`. The refactor lifts these to module-level functions taking `services` as their first non-task argument.

### `TaskRouter` after refactor

```python
class TaskRouter:
    def __init__(self, store, dispatcher, worktree_mgr, memory, ...):
        # Construct services bundle once at init
        self._services = PhaseServices(
            store=store,
            memory=memory,
            channels=channel_registry,
            reviewer_invoker=reviewer_invoker or _default_review,
            transition=self._transition,
            safe_transition=self._safe_transition,
            notify=self._notify_originating_channel,
        )
        # ... rest unchanged

    def run_task(self, task_id: str) -> TaskRecord:
        # ... INBOUND → TRIAGING → ROUTED → DECOMPOSED stays here ...

        if t.workflow == "research":
            research_phase.run(t, self._services)
            return self._store.get_task(task_id)
        if t.workflow == "content":
            content_phase.run(t, self._services)
            return self._store.get_task(task_id)
        if t.workflow == "ops":
            ops_phase.run(t, self._services)
            return self._store.get_task(task_id)

        # ... main builder/reviewer path stays here ...

        if t.workflow == "dev":
            return dev_phase.run_pr_phase(
                task=t, plan_obj=plan_obj, findings=findings,
                worktree_path=worktree_path, repo_path=repo_path,
                services=self._services,
            )
        # ... else branch unchanged ...

    def handle_approval(self, task_id: str, decision: ApprovalDecision) -> TaskRecord:
        task = self._store.get_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")

        if task.state == TaskState.AWAITING_OWNER_APPROVAL and task.workflow == "ops":
            return ops_phase.handle_approval(task, decision=decision, services=self._services)
        if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "content":
            return content_phase.handle_approval(task, decision, self._services)
        if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "dev":
            return dev_phase.handle_approval(task, decision, self._services)
        raise NotImplementedError(
            f"approval for task {task_id!r} in state {task.state!r} "
            f"workflow={task.workflow!r} not implemented"
        )
```

### Cross-module dependencies

`dev_phase.run_pr_phase` needs `_format_pr_body` and `_render_builder_prompt`. Both move with it:
- `_format_pr_body` is currently a module-level function in router.py → moves to `dev_phase.py`
- `_render_builder_prompt` reads `builder_prompt_path` from `self` → becomes a free function in `dev_phase.py` that takes the path as an argument. The path lives on the services bundle (`services.builder_prompt_path`).

Wait — `builder_prompt_path` is only used by the main builder loop (which stays in `TaskRouter.run_task`) and not by `_run_dev_pr_phase`. Verify during implementation: `_render_builder_prompt` is only used at L250 of `run_task`. So `_render_builder_prompt` and `builder_prompt_path` stay on `TaskRouter`. The dev phase only needs `_format_pr_body`, which is already a free function — it moves cleanly.

The main builder/reviewer flow (`run_task` lines 192-328) stays in `TaskRouter` because it's shared infrastructure used by ALL non-research/content/ops workflows (the "default" path and the dev workflow). Only the workflow-specific PR-opening logic moves to `dev_phase`.

### `_repo_path_for_workflow` access

`dev_phase.run_pr_phase` and `dev_phase.handle_approval` both call `self._repo_path_for_workflow(task.workflow)`. Add this callable to `PhaseServices`:

```python
@dataclass(frozen=True)
class PhaseServices:
    # ...
    repo_path_for_workflow: Callable[[str], Path]
```

### `workflows` list access

`ops_phase.run` calls `load_rules(workflows_dir / "ops" / "risk-rules.yaml")`. The path is currently derived from `__file__` in `_run_ops_phase`. Pass the workflows-dir explicitly via `PhaseServices.workflows_dir: Path`, computed once at `TaskRouter.__init__`. This makes the refactor testable with a fixture workflows dir.

## Data flow

Identical to current. State transitions happen via `services.transition()`. Memory events happen via `services.memory.emit()`. Worker dispatch happens via `services.dispatcher.dispatch()`. The phase runners produce no new state — they call the same primitives that `TaskRouter` currently calls inline.

## Error handling

Unchanged. The existing `try/except BudgetExceeded/WorkerProducedNothing/Exception` in `run_task` wraps the entire happy path (including the phase-runner branches). Phase runners raise; `run_task` catches and transitions to `COST_PAUSED`/`FAILED`.

Phase runners do NOT add their own try/except — they would mask errors and complicate the cost-tracker accounting. Failure transitions remain centralized.

## Testing strategy

The integration tests at `tests/integration/test_router_research.py`, `test_router_content.py`, `test_router_ops.py`, `test_router_dev.py` all exercise `TaskRouter.run_task` end-to-end with stub backends. **They must pass byte-for-byte unchanged** after the refactor — that's how we know we didn't change behavior.

Additionally, each new phase module gets a focused unit test file that calls the phase function directly with a constructed `PhaseServices` mock. These unit tests overlap with the integration tests but allow tighter assertions on the per-phase contract (e.g., "ops_phase.run never executes if classifier returns critical").

Expected delta: 190 existing tests stay at 190 passing; 4 new phase unit-test files add ~20 tests; final ~210 tests.

## Migration notes

The refactor is mechanical and pure-cut: take a block of code from router.py, replace `self._store` with `services.store` / `self._memory` with `services.memory` / `self._transition(...)` with `services.transition(...)` / `self._channels` with `services.channels`, paste into the new module. No logic changes.

The `_handle_ops_approval` method takes `approver_role` and `rationale` as positional/keyword args. Move it as-is — the call site in router's `handle_approval` (lines 1105-1121) translates `ApprovalDecision.gate` to `approver_role`. That translation logic moves into `ops_phase.handle_approval` so the public contract via `ApprovalDecision` stays the only surface area.

## What stays in `TaskRouter`

After the refactor, `TaskRouter` owns:
1. The state machine spine: `INBOUND → TRIAGING → ROUTED → DECOMPOSED → DISPATCHED → RUNNING → REVIEWED → DELIVERABLE_READY` for the **default builder/reviewer flow** (i.e., the path no phase runner takes over).
2. Approval dispatch (`handle_approval` thin router).
3. Cross-cutting helpers: `_notify_originating_channel`, `_format_notify_body`, `_transition`, `_safe_transition`.
4. Builder-prompt rendering + diff computation (specific to the default flow).

This means `TaskRouter` is no longer the workflow controller — it's the **state-machine coordinator and approval router**. Phase modules own workflow-specific logic.

## Out of scope

- No new behavior, features, or workflows.
- No state-machine changes.
- No public-API changes to `TaskRouter`.
- No changes to `research.py`/`content.py`/`ops.py` (the LLM helper modules).
- No changes to `state.py`, `types.py`, `dispatcher.py`, or any storage schema.
- No router/web-server changes.
- No prompts or YAML changes.

This is a pure structural refactor. Behavior verification is "every existing test passes unchanged."

## Risks

1. **Subtle drift via copy-paste error.** Mitigation: integration tests catch it; the implementer must run the full suite at each commit.
2. **Circular import.** `dev_phase.py` imports from `state`, `types`, `pr`. `research_phase.py` imports from `research`, `types`. `ops_phase.py` imports from `ops`, `risk_tier`, `audit`. None of these import `router.py` back. Verify no circular import during implementation.
3. **`PhaseServices` becomes a god-bundle.** Mitigation: enumerate only what's actually used by ≥1 phase. If a service is only used by one phase, pass it directly to that function. Current proposed surface: 9 fields — manageable.
4. **Test churn.** `test_router_*.py` tests construct `TaskRouter` and call `run_task`. They should still pass without modification. If they don't, that's a behavior regression, not a refactoring need.

## Self-review (inline)

- Placeholders: none.
- Internal consistency: `PhaseServices` field list above matches the call-site analysis in §"After".
- Scope: single focused refactor, fits one plan, one PR.
- Ambiguity: `_render_builder_prompt` location resolved in §"Cross-module dependencies".
