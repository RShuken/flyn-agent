# Cookbook: Add a new workflow

A "workflow" is a per-domain orchestration pipeline (dev, research, content, ops). Each workflow has:
- A **policy file** declaring its roles, flow steps, and budget
- One or more **role prompts** the orchestrator feeds to `claude -p` workers
- A **phase-runner module** owning the state-machine glue
- A **router branch** that dispatches tasks tagged with this workflow
- Tests covering the happy path + the most likely failure paths

After this guide, you'll have a `<name>_phase.py` module that ships in a single PR.

## When to add a workflow

You're adding a workflow when the task domain has a **distinct role lineup or flow** that doesn't fit the existing four. Examples that would warrant new workflows:

- **`legal`** — contract review pipeline (Reader → Issue-spotter → Risk-tier classifier → Redliner → Final-counsel)
- **`support`** — customer ticket triage (Triager → Diagnostic → Solution-writer → QA → Sender)
- **`analytics`** — data-question answering (PM → SQL-author → Critic → Visualizer → Synthesizer)

If your domain fits an existing workflow (e.g., "write a blog post" is just `content`), don't add a workflow — add prompt variants instead.

## Decide the shape

Three questions in order:

1. **What are the roles?** Each role becomes a `claude -p` invocation with a dedicated prompt. Conventionally: a PM-style role that turns the user intent into a structured spec; one or more "doers"; one or more fresh-context reviewers/critics. Always include at least one fresh-context reviewer — that's Flyn's differentiator.

2. **What's the state machine?** Will the workflow ever pause for human approval? If yes, you need an `*_PENDING` terminal state and a `handle_approval` entry point. If no, the happy path is `DECOMPOSED → DISPATCHED → RUNNING → REVIEWED → DELIVERABLE_READY`.

3. **What does "done" produce?** A file on disk? A PR? A Telegram message? An audit-log row? This determines what your phase runner does after the last role.

## Build it — step by step

### 1. Workflow policy YAML

Create `deploy/orchestrator/flyn_orchestrator/workflows/<name>.yaml`:

```yaml
# Phase X <name> workflow policy.
name: <name>
intent_patterns:
  - "pattern1"        # short regex/substring matches that route tasks to this workflow
  - "pattern2"
roles:
  - name: pm
    model: claude
    prompt: pm_<name>
  - name: doer
    model: claude
    prompt: <name>_doer
  - name: critic
    model: claude
    prompt: <name>_critic
    readonly: true            # critic should not have edit tools
flow:
  - intake
  - spec
  - run
  - critique
approval_gates:
  default: teammate           # who can approve? "owner" / "teammate" / "owner_with_dry_run"
budget_default_usd: 2.0       # per-task ceiling
```

Look at `workflows/research.yaml` and `workflows/ops.yaml` for the two ends of the complexity spectrum.

### 2. Role prompts

Create `deploy/orchestrator/flyn_orchestrator/prompts/pm_<name>.md`, `<name>_doer.md`, `<name>_critic.md`. Each is a self-contained instruction file the worker reads as its system prompt.

**Anatomy of a good role prompt:**
- **One-line identity statement** ("You are the PM for Flyn's <name> workflow.")
- **What you receive** — describe the input format the orchestrator will inject (intent, prior outputs, etc.)
- **What you return** — emit JSON in a specific shape. Use code fences. Specify required fields.
- **What you DON'T do** — be explicit about scope boundaries (e.g., "Do not invent citations" for research critic; "Do not auto-send" for content writer)
- **Failure mode** — "If the input is ambiguous, return `{\"title\": \"(ambiguous)\"}` so the orchestrator can fail the task cleanly."

The orchestrator's `_extract_json_block(raw_response)` helper in `citations.py` parses the first ```json ... ``` block in the worker's output. Your prompt should produce exactly one such block.

### 3. LLM helper module

Create `deploy/orchestrator/flyn_orchestrator/<name>.py` with pure orchestration helpers — **no state machine logic here**, just role-dispatch.

Pattern (research.py / content.py / ops.py are reference implementations):

```python
# flyn_orchestrator/<name>.py
"""<name> workflow orchestration helpers.

Pure functions. Each takes a backend + scratch_dir + task_id and returns
a typed result. No state-machine transitions; those happen in <name>_phase.py.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .backends.base import WorkerBackend, WorkerResult
from .citations import _extract_json_block
from .types import WorkerRole, WorkerSpec


@dataclass(frozen=True)
class <Name>Spec:
    """What the PM produces from the intent."""
    title: str
    # ... other fields specific to your domain


def spec_<name>(
    intent: str,
    *,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str,
) -> Optional[<Name>Spec]:
    """Invoke the PM role; parse output; return <Name>Spec or None on failure."""
    worker_id = f"{task_id}-pm"
    spec = WorkerSpec(
        task_id=task_id, worker_id=worker_id, role=WorkerRole.PM,
        backend=backend.name, prompt_template="pm_<name>",
        worktree_path=str(scratch_dir),
        max_turns=3, budget_usd=0.5,
        allowed_tools=["Read"],
    )
    prompt = _build_pm_prompt(intent)  # construct full prompt with intent embedded
    result = backend.run(spec, prompt)
    raw = result.capture_path.read_text() if result.capture_path.exists() else ""
    parsed = _extract_json_block(raw)
    if not parsed:
        return None
    try:
        return <Name>Spec(
            title=parsed.get("title", "(ambiguous)"),
            # ... fill remaining fields
        )
    except (KeyError, TypeError):
        return None
```

Add one helper per role. Keep them stateless and testable.

### 4. Phase-runner module

Create `deploy/orchestrator/flyn_orchestrator/<name>_phase.py`. **This is where the state-machine glue lives.**

Pattern (research_phase.py is the cleanest reference):

```python
# flyn_orchestrator/<name>_phase.py
"""<name>-workflow phase runner.

Walks the state machine for this workflow:
  DECOMPOSED → DISPATCHED → RUNNING → REVIEWED → DELIVERABLE_READY
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from .<name> import spec_<name>, do_<name>, critique_<name>   # the helpers from step 3
from .types import TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


def run(task: TaskRecord, services: "PhaseServices") -> None:
    """Walk the <name> workflow's state machine. Idempotent transitions."""
    backend = services.backend_registry.get("claude-p")
    scratch = services.scratch_root / task.task_id
    scratch.mkdir(parents=True, exist_ok=True)

    # 1. Spec (PM)
    services.safe_transition(
        task.task_id, TaskState.DECOMPOSED, TaskState.DISPATCHED,
        actor="<name>", reason="PM speccing",
    )
    spec = spec_<name>(task.intent, scratch_dir=scratch, backend=backend, task_id=task.task_id)
    if spec is None or spec.title.startswith("("):
        services.safe_transition(
            task.task_id, TaskState.DISPATCHED, TaskState.FAILED,
            actor="<name>", reason="PM spec unparseable",
        )
        services.memory.emit(
            source="orchestrator", event_type="task_failed",
            subject=task.task_id, body="<name> PM step failed",
            dedup_key=f"orch-{task.task_id}-pm-fail", importance="warm",
        )
        return

    # ... continue with the rest of your flow steps ...

    # Final transition + memory emit + notify
    services.safe_transition(
        task.task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
        actor="router", reason="...",
    )
    services.memory.emit(...)
    services.notify(services.store.get_task(task.task_id), None, ...)
```

**Key rules for phase runners:**
- Take `(task, services)` only. `services` is a frozen `PhaseServices` bundle — never reach past it.
- Use `services.safe_transition` (not `services.transition`) on every transition. `safe_transition` is idempotent; safe to retry.
- Every state transition gets a memory event.
- Catch nothing — let the caller's `run_task` exception handler manage FAILED/COST_PAUSED transitions.
- If your workflow has an approval gate, add a `handle_approval(task, decision, services) -> TaskRecord` function in the same file.

### 5. Router branch

In `router.py`, add the dispatch branch in `run_task` (after the existing ops/content/research checks):

```python
if t.workflow == "<name>":
    from . import <name>_phase
    <name>_phase.run(t, self._services)
    return self._store.get_task(task_id)
```

If your workflow has approvals, add to `handle_approval`:

```python
if task.state == TaskState.<YOUR_PENDING_STATE> and task.workflow == "<name>":
    from . import <name>_phase
    return <name>_phase.handle_approval(task, decision, self._services)
```

### 6. Tests

Create `deploy/orchestrator/tests/integration/test_router_<name>.py`. Use the pattern from `test_router_ops.py` — a fixture builds a `TaskRouter` wired to a stub backend; tests submit a task via `router.accept(InboundTaskRequest(...))` then `router.run_task(task_id)` and assert on the final state + audit log.

Cover at minimum:
- Happy path → `DELIVERABLE_READY`
- PM-emits-ambiguous → `FAILED`
- One blocking critique → `CHANGES_REQUESTED` (or your workflow's equivalent)
- If approvals: approval-rejected path + approval-accepted path

### 7. Ship checklist

- [ ] `workflows/<name>.yaml` policy
- [ ] `prompts/<name>_*.md` role prompts (at least PM + doer + critic)
- [ ] `<name>.py` LLM helper module
- [ ] `<name>_phase.py` phase runner with `run` (+ `handle_approval` if needed)
- [ ] Router branch in `run_task` (+ `handle_approval` branch if approvals)
- [ ] Integration tests `test_router_<name>.py`
- [ ] Rubric updated: add Phase N row + criteria; update overall total
- [ ] `audit/_baseline.md` §Δ subsection appended (new patterns + new threats)
- [ ] Ship-gate playbook at `deploy/orchestrator/tests/e2e/test_phase_N_ship_gate.md` (15 steps with curl-against-`:8300`)
- [ ] Memory entry at `KNOWLEDGE/<NN>-<slug>.md` for any hard-won lesson surfaced

## Anti-patterns to avoid

- **Putting state-machine glue in `<name>.py`.** That file is pure helpers. State transitions go in `<name>_phase.py`.
- **Mixing role prompts with logic.** Role prompts are pure markdown read by the worker. Don't embed business logic there; put it in the phase runner.
- **Catching exceptions in the phase runner.** Let `run_task` decide whether to transition to FAILED / COST_PAUSED. Phase runners just raise.
- **Reaching past `services`.** If you find yourself wanting `services._dispatcher.something`, that's a sign — either extend `PhaseServices` with what you need, or rethink whether it belongs in the phase runner.

## See also

- `KNOWLEDGE/18-cross-module-mock-patching.md` — how to write tests that survive future refactors
- `KNOWLEDGE/19-test-the-public-api-not-internals.md` — why your tests should call `router.run_task` not `_run_<name>_phase`
- `docs/superpowers/specs/2026-05-15-flyn-orchestrator-design.md` §3 — the broader phase architecture
