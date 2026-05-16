# Phase 2c Router Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract per-workflow phase logic out of `router.py` (1,398 lines) into 4 focused phase-runner modules. `TaskRouter` becomes the state-machine coordinator and approval dispatcher only.

**Architecture:** Function-based phase runners. Each phase module exports module-level functions taking `(task, services)` where `services` is a frozen `PhaseServices` dataclass bundling shared dependencies. Mirrors the existing `research.py`/`content.py`/`ops.py` helper-module pattern. No new behavior; all 190 existing tests pass byte-for-byte unchanged.

**Tech Stack:** Python 3.11+, dataclasses, type hints.

**Spec:** `docs/superpowers/specs/2026-05-16-flyn-orchestrator-phase-2c-router-refactor-design.md`

---

## Task 1: PhaseServices dataclass

**Files:**
- Create: `deploy/orchestrator/flyn_orchestrator/phase_services.py`
- Test: `deploy/orchestrator/tests/unit/test_phase_services.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/orchestrator/tests/unit/test_phase_services.py
from pathlib import Path
from flyn_orchestrator.phase_services import PhaseServices


def test_phase_services_is_frozen():
    """Frozen dataclass: mutation raises FrozenInstanceError."""
    import dataclasses
    svc = PhaseServices(
        store=object(),
        memory=object(),
        channels=None,
        reviewer_invoker=lambda **kw: None,
        transition=lambda *a, **kw: None,
        safe_transition=lambda *a, **kw: None,
        notify=lambda *a, **kw: None,
        backend_registry=object(),
        scratch_root=Path("/tmp"),
        repo_path_for_workflow=lambda w: Path("/tmp"),
        workflows_dir=Path("/tmp"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.store = "mutated"  # type: ignore


def test_phase_services_exposes_expected_fields():
    """The 11-field surface is the contract phase runners depend on."""
    from dataclasses import fields
    field_names = {f.name for f in fields(PhaseServices)}
    assert field_names == {
        "store", "memory", "channels", "reviewer_invoker",
        "transition", "safe_transition", "notify",
        "backend_registry", "scratch_root", "repo_path_for_workflow",
        "workflows_dir",
    }
```

Add `import pytest` at top.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/4c/AI/openclaw/flyn-agent-p5
source deploy/orchestrator/.venv/bin/activate
python -m pytest deploy/orchestrator/tests/unit/test_phase_services.py -v
```

Expected: FAIL with `ModuleNotFoundError: phase_services`.

- [ ] **Step 3: Write minimal implementation**

```python
# deploy/orchestrator/flyn_orchestrator/phase_services.py
"""Shared services bundle passed to phase-runner modules.

Frozen dataclass: phase runners read but never mutate. Eliminates threading
8+ individual arguments through every phase function signature, and avoids
coupling phase modules to the TaskRouter class itself.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters import ChannelRegistry
    from .backends import BackendRegistry
    from .memory import MemoryEmitter
    from .state import StateStore
    from .types import ReviewFindings, TaskState


@dataclass(frozen=True)
class PhaseServices:
    store: "StateStore"
    memory: "MemoryEmitter"
    channels: Optional["ChannelRegistry"]
    reviewer_invoker: Callable[..., "ReviewFindings"]
    transition: Callable[..., None]
    safe_transition: Callable[..., None]
    notify: Callable[..., None]
    backend_registry: "BackendRegistry"
    scratch_root: Path
    repo_path_for_workflow: Callable[[str], Path]
    workflows_dir: Path
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest deploy/orchestrator/tests/unit/test_phase_services.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add deploy/orchestrator/flyn_orchestrator/phase_services.py \
        deploy/orchestrator/tests/unit/test_phase_services.py
git commit -m "feat(orchestrator): PhaseServices dataclass — shared bundle for phase runners

Phase 2c T01. Frozen dataclass with 11 fields enumerates exactly what
phase modules need from the router. Replaces per-function arg lists in
upcoming phase_*.py modules.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Extract research_phase.py

**Files:**
- Create: `deploy/orchestrator/flyn_orchestrator/research_phase.py`
- Modify: `deploy/orchestrator/flyn_orchestrator/router.py` (replace `_run_research_phase` method body with delegation)
- Test: existing `deploy/orchestrator/tests/integration/test_router_research.py` (must pass unchanged)

- [ ] **Step 1: Copy `_run_research_phase` body into a new module-level `run()` function**

Create `deploy/orchestrator/flyn_orchestrator/research_phase.py` with this content (translates `self._store` → `services.store`, etc.):

```python
# deploy/orchestrator/flyn_orchestrator/research_phase.py
"""Research-workflow phase runner.

Walks the 5-step research flow:
  DECOMPOSED → DISPATCHED → RUNNING → REVIEWED → DELIVERABLE_READY | CHANGES_REQUESTED
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from .research import decompose_intent, run_researchers, critique, synthesize, write_output
from .types import TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


def run(task: TaskRecord, services: "PhaseServices") -> None:
    """Walk the research workflow's state machine. Idempotent transitions."""
    backend = services.backend_registry.get("claude-p")
    scratch = services.scratch_root / task.task_id
    scratch.mkdir(parents=True, exist_ok=True)

    # 1. Decompose
    services.safe_transition(
        task.task_id, TaskState.DECOMPOSED, TaskState.DISPATCHED,
        actor="router", reason="research: PM decomposing",
    )
    plan = decompose_intent(
        task.intent, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    if plan is None or not plan.sub_questions:
        services.safe_transition(
            task.task_id, TaskState.DISPATCHED, TaskState.FAILED,
            actor="research", reason="PM output unparseable or empty",
        )
        services.memory.emit(
            source="orchestrator", event_type="task_failed",
            subject=task.task_id, body="research PM step failed",
            dedup_key=f"orch-{task.task_id}-pm-fail", importance="warm",
        )
        return

    # 2. Researchers
    services.safe_transition(
        task.task_id, TaskState.DISPATCHED, TaskState.RUNNING,
        actor="research", reason=f"running {len(plan.sub_questions)} researchers",
    )
    outputs = run_researchers(
        plan, scratch_dir=scratch, backend=backend,
        task_id=task.task_id, max_parallel=4,
    )
    if not outputs:
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.FAILED,
            actor="research", reason="no researcher outputs",
        )
        return

    # 3. Critique
    services.safe_transition(
        task.task_id, TaskState.RUNNING, TaskState.REVIEWED,
        actor="research", reason=f"got {len(outputs)} researcher outputs",
    )
    critique_result = critique(
        plan, outputs, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    services.memory.emit(
        source="orchestrator", event_type="critique_complete",
        subject=task.task_id,
        body=f"critique passed={critique_result.passed}; "
             f"{len(critique_result.findings)} findings",
        dedup_key=f"orch-{task.task_id}-critique", importance="warm",
    )
    if not critique_result.passed:
        critical_findings = [
            f for f in critique_result.findings
            if f.severity in ("critical", "important")
        ]
        services.safe_transition(
            task.task_id, TaskState.REVIEWED, TaskState.CHANGES_REQUESTED,
            actor="critic",
            reason=f"critique failed: {len(critical_findings)} blocking findings",
        )
        return

    # 4. Synthesize
    minor = [f for f in critique_result.findings if f.severity in ("minor", "info")]
    report_md = synthesize(
        title=plan.title, requester=task.sender_identifier,
        task_id=task.task_id, rationale=plan.rationale, outputs=outputs,
        minor_findings=minor, scratch_dir=scratch, backend=backend,
    )

    # 5. Write output
    report_path = write_output(
        report_md=report_md, outputs=outputs, title=plan.title, task_id=task.task_id,
    )
    services.store.update_task_payload(task.task_id, {
        "report_path": str(report_path),
        "research_title": plan.title,
    })
    services.safe_transition(
        task.task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
        actor="router", reason=f"report at {report_path}",
    )
    services.memory.emit(
        source="orchestrator", event_type="research_complete",
        subject=task.task_id,
        body=f"Research report '{plan.title}' delivered to {report_path}",
        dedup_key=f"orch-{task.task_id}-research", importance="warm",
    )
    services.notify(
        services.store.get_task(task.task_id), None,
        research_report_path=str(report_path),
        research_summary=report_md[:1500],
    )
```

- [ ] **Step 2: Replace `_run_research_phase` in router.py with delegation**

Edit `deploy/orchestrator/flyn_orchestrator/router.py`. Replace the entire `_run_research_phase` method (lines ~452-558) with:

```python
    def _run_research_phase(self, task: TaskRecord) -> None:
        from . import research_phase
        research_phase.run(task, self._services)
```

(The `self._services` field is added in Task 6. For now, construct it inline at the top of the method:)

```python
    def _run_research_phase(self, task: TaskRecord) -> None:
        from . import research_phase
        research_phase.run(task, self._make_services())
```

And add a helper method on TaskRouter:

```python
    def _make_services(self):
        """Build the PhaseServices bundle on demand. Will be cached in T06."""
        from .phase_services import PhaseServices
        return PhaseServices(
            store=self._store,
            memory=self._memory,
            channels=self._channels,
            reviewer_invoker=self._reviewer_invoker,
            transition=self._transition,
            safe_transition=self._safe_transition,
            notify=self._notify_originating_channel,
            backend_registry=self._dispatcher._registry,
            scratch_root=Path(self._wt_mgr._dir),
            repo_path_for_workflow=self._repo_path_for_workflow,
            workflows_dir=Path(__file__).parent / "workflows",
        )
```

- [ ] **Step 3: Run integration tests for research workflow**

```bash
python -m pytest deploy/orchestrator/tests/integration/test_router_research.py -v
```

Expected: ALL PASS (same count as before).

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest deploy/orchestrator/tests/ 2>&1 | tail -3
```

Expected: 192 passed (190 prior + 2 from Task 1).

- [ ] **Step 5: Commit**

```bash
git add deploy/orchestrator/flyn_orchestrator/research_phase.py \
        deploy/orchestrator/flyn_orchestrator/router.py
git commit -m "refactor(orchestrator): extract research_phase.py from router

Phase 2c T02. _run_research_phase becomes a thin delegation to
research_phase.run(task, services). No behavior change — same 5-step
flow, same state transitions, same memory events. Router loses ~107
lines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Extract content_phase.py

**Files:**
- Create: `deploy/orchestrator/flyn_orchestrator/content_phase.py`
- Modify: `deploy/orchestrator/flyn_orchestrator/router.py` (replace `_run_content_phase` body; replace `handle_approval` content branch)
- Test: existing integration tests must pass unchanged

- [ ] **Step 1: Create content_phase.py**

Create `deploy/orchestrator/flyn_orchestrator/content_phase.py`. Copy the entire body of `_run_content_phase` (lines ~564-726) into a module-level `run(task, services)` function. Then copy the content branch of `handle_approval` (lines ~1123-1172) into a module-level `handle_approval(task, decision, services)` function.

Translation rules:
- `self._store` → `services.store`
- `self._memory` → `services.memory`
- `self._channels` → `services.channels`
- `self._safe_transition(...)` → `services.safe_transition(...)`
- `self._notify_originating_channel(...)` → `services.notify(...)`
- `self._dispatcher._registry.get("claude-p")` → `services.backend_registry.get("claude-p")`
- `self._wt_mgr._dir` → `services.scratch_root`

`_slugify_for_content` is currently imported at the bottom of router.py — confirm its location by running:
```bash
grep -n "_slugify_for_content" deploy/orchestrator/flyn_orchestrator/router.py
```
Move the import to `content_phase.py` and remove it from `router.py` if no longer used there.

Full file:

```python
# deploy/orchestrator/flyn_orchestrator/content_phase.py
"""Content-workflow phase runner.

Walks the content workflow's 8-phase sequential pipeline:
  DECOMPOSED → DISPATCHED → RUNNING → CHANGES_REQUESTED | DELIVERABLE_READY | FINAL_APPROVAL_PENDING

Approval handler routes FINAL_APPROVAL_PENDING → COMPLETED (sent) or CANCELLED.
"""
from __future__ import annotations
import json as _json
import os
import re as _re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .content import spec_content, draft_content, edit_content, fact_check_content, humanize_content
from .formatting import format_for_platform
from .types import ApprovalDecision, TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


def run(task: TaskRecord, services: "PhaseServices") -> None:
    """Walk the content workflow's state machine."""
    # ... full body translated from _run_content_phase ...


def handle_approval(
    task: TaskRecord,
    decision: ApprovalDecision,
    services: "PhaseServices",
) -> TaskRecord:
    """Handle FINAL_APPROVAL_PENDING for content: send draft or cancel."""
    # ... full body translated from handle_approval's content branch ...
```

Inline the `_slugify_for_content` helper into content_phase.py (or re-import from wherever it lives — discover via grep).

- [ ] **Step 2: Replace router methods with delegation**

In `router.py`, replace `_run_content_phase` body with:

```python
    def _run_content_phase(self, task: TaskRecord) -> None:
        from . import content_phase
        content_phase.run(task, self._make_services())
```

In `handle_approval`, replace the content branch (the `if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "content":` block) with:

```python
        if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "content":
            from . import content_phase
            return content_phase.handle_approval(task, decision, self._make_services())
```

- [ ] **Step 3: Run integration tests**

```bash
python -m pytest deploy/orchestrator/tests/integration/test_router_content.py -v
python -m pytest deploy/orchestrator/tests/ 2>&1 | tail -3
```

Expected: all integration tests pass; full suite still 192.

- [ ] **Step 4: Commit**

```bash
git add deploy/orchestrator/flyn_orchestrator/content_phase.py \
        deploy/orchestrator/flyn_orchestrator/router.py
git commit -m "refactor(orchestrator): extract content_phase.py from router

Phase 2c T03. _run_content_phase and the content branch of
handle_approval move to content_phase.run() and content_phase.
handle_approval(). Router loses ~225 lines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Extract ops_phase.py

**Files:**
- Create: `deploy/orchestrator/flyn_orchestrator/ops_phase.py`
- Modify: `deploy/orchestrator/flyn_orchestrator/router.py` (replace `_run_ops_phase`, `_execute_ops_and_finalize`, `_handle_ops_approval`; replace ops branch in `handle_approval`)

- [ ] **Step 1: Create ops_phase.py**

Create the file with these three functions:

```python
# deploy/orchestrator/flyn_orchestrator/ops_phase.py
"""Ops-workflow phase runner.

Walks the ops workflow's risk-tier-gated pipeline with audit log:
  DECOMPOSED → DISPATCHED → RUNNING → AWAITING_OWNER_APPROVAL | DELIVERABLE_READY

Critical-tier requires owner + written rationale; medium/high allow
owner-or-teammate. Low tier auto-executes. One-way escalation enforced
in ops.classify_risk via max_tier().
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import audit as _audit
from . import ops as _ops
from .risk_tier import load_rules
from .types import ApprovalDecision, TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


# Auth tier sets — kept module-private since they are the contract of
# the auth check inside handle_approval.
_OWNER_ROLES = frozenset({"owner"})
_TEAMMATE_OR_OWNER_ROLES = frozenset({"owner", "teammate"})


def run(task: TaskRecord, services: "PhaseServices") -> None:
    """Walk the ops workflow's state machine."""
    # ... translated from _run_ops_phase, replacing self._ → services. ...
    # rules path: services.workflows_dir / "ops" / "risk-rules.yaml"


def execute_and_finalize(
    task: TaskRecord,
    *,
    spec: _ops.OpsSpec,
    tier: str,
    before_snap: _audit.SnapshotBundle,
    scratch: Path,
    backend,
    services: "PhaseServices",
) -> None:
    """Execute → post-snapshot → validate → DELIVERABLE_READY or AWAITING_OWNER_APPROVAL."""
    # ... translated from _execute_ops_and_finalize ...


def handle_approval(
    task: TaskRecord,
    decision: ApprovalDecision,
    services: "PhaseServices",
) -> TaskRecord:
    """Handle AWAITING_OWNER_APPROVAL for ops: enforce auth + resume or reject.

    Translates ApprovalDecision.gate to approver_role: "owner" or "critical"
    gates indicate an owner-level approval; everything else is treated as
    teammate.
    """
    payload = task.raw_payload or {}
    tier = payload.get("risk_tier", "medium")
    approver_role = "owner" if decision.gate in ("owner", "critical") else "teammate"
    decision_str = "approve" if decision.approved else "reject"
    rationale = decision.reason

    return _handle_approval_impl(
        task=task, approver=decision.approver,
        decision=decision_str, approver_role=approver_role,
        rationale=rationale, services=services,
    )


def _handle_approval_impl(
    task: TaskRecord,
    *,
    approver: str,
    decision: str,
    approver_role: str,
    rationale: Optional[str],
    services: "PhaseServices",
) -> TaskRecord:
    # ... translated from _handle_ops_approval ...
    # Auth check uses _OWNER_ROLES / _TEAMMATE_OR_OWNER_ROLES.
    # Calls execute_and_finalize(...) on approve.
```

Translation specifics:
- The class constants `_OPS_RISK_RULES_PATH = Path(__file__).parent / "workflows" / "ops" / "risk-rules.yaml"` becomes `services.workflows_dir / "ops" / "risk-rules.yaml"` inside `run()`.
- The class constants `_OWNER_ROLES`, `_TEAMMATE_OR_OWNER_ROLES` become module-level constants.
- `self._execute_ops_and_finalize(...)` becomes `execute_and_finalize(task, spec=spec, tier=tier, ..., services=services)`.

- [ ] **Step 2: Replace router methods**

In `router.py`:
1. Delete `_run_ops_phase`, `_execute_ops_and_finalize`, `_handle_ops_approval` methods.
2. Replace the `if t.workflow == "ops":` branch in `run_task` with:

```python
            if t.workflow == "ops":
                from . import ops_phase
                ops_phase.run(t, self._make_services())
                return self._store.get_task(task_id)
```

3. Replace the ops branch in `handle_approval` (`if task.state == TaskState.AWAITING_OWNER_APPROVAL and task.workflow == "ops":`) with:

```python
        if task.state == TaskState.AWAITING_OWNER_APPROVAL and task.workflow == "ops":
            from . import ops_phase
            return ops_phase.handle_approval(task, decision, self._make_services())
```

4. Delete the class constants `_OPS_WORKFLOWS_DIR`, `_OPS_RISK_RULES_PATH`, `_OWNER_ROLES`, `_TEAMMATE_OR_OWNER_ROLES` from `TaskRouter`.

- [ ] **Step 3: Run integration tests**

```bash
python -m pytest deploy/orchestrator/tests/integration/test_router_ops.py -v
python -m pytest deploy/orchestrator/tests/ 2>&1 | tail -3
```

Expected: all integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add deploy/orchestrator/flyn_orchestrator/ops_phase.py \
        deploy/orchestrator/flyn_orchestrator/router.py
git commit -m "refactor(orchestrator): extract ops_phase.py from router

Phase 2c T04. _run_ops_phase, _execute_ops_and_finalize, and
_handle_ops_approval move to ops_phase.{run,execute_and_finalize,
handle_approval}. ApprovalDecision-to-approver_role translation lives
in handle_approval where it belongs. Router loses ~350 lines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Extract dev_phase.py

**Files:**
- Create: `deploy/orchestrator/flyn_orchestrator/dev_phase.py`
- Modify: `deploy/orchestrator/flyn_orchestrator/router.py` (replace `_run_dev_pr_phase`; replace dev branch in `handle_approval`; remove `_format_pr_body`)

- [ ] **Step 1: Create dev_phase.py**

```python
# deploy/orchestrator/flyn_orchestrator/dev_phase.py
"""Dev-workflow PR phase runner.

After the main builder/reviewer flow lands at REVIEWED, dev workflow pushes
the branch and opens a PR. On approval, the PR merges and the task transitions
to COMPLETED.
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .pr import PRError, create_pr, merge_pr, pr_number_from_url
from .types import ApprovalDecision, ReviewFindings, TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


def _format_pr_body(task: TaskRecord, plan: dict, review: ReviewFindings) -> str:
    """Render PR body with task metadata + reviewer findings."""
    # ... moved verbatim from router.py module-level _format_pr_body ...


def run_pr_phase(
    *,
    task_id: str,
    task: TaskRecord,
    plan_obj: dict,
    findings: ReviewFindings,
    worktree_path: Path,
    repo_path: Path,
    services: "PhaseServices",
) -> TaskRecord:
    """Push branch + open PR + transition to FINAL_APPROVAL_PENDING.

    Falls back to DELIVERABLE_READY on push or PR-create failure.
    """
    # ... translated from _run_dev_pr_phase ...


def handle_approval(
    task: TaskRecord,
    decision: ApprovalDecision,
    services: "PhaseServices",
) -> TaskRecord:
    """Handle FINAL_APPROVAL_PENDING for dev: merge PR or cancel."""
    # ... translated from handle_approval's dev branch ...
    # Uses services.repo_path_for_workflow(task.workflow) for merge_pr.
```

- [ ] **Step 2: Replace router methods**

In `router.py`:
1. Delete `_run_dev_pr_phase` method.
2. Delete the module-level `_format_pr_body` function (now in `dev_phase.py`).
3. Replace `_run_dev_pr_phase` call site in `run_task` with:

```python
            if t.workflow == "dev":
                from . import dev_phase
                return dev_phase.run_pr_phase(
                    task_id=task_id, task=t, plan_obj=plan_obj,
                    findings=findings, worktree_path=worktree_path,
                    repo_path=repo_path, services=self._make_services(),
                )
```

4. Replace the dev branch in `handle_approval` (`if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "dev":`) with:

```python
        if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "dev":
            from . import dev_phase
            return dev_phase.handle_approval(task, decision, self._make_services())
```

- [ ] **Step 3: Run integration tests**

```bash
python -m pytest deploy/orchestrator/tests/integration/test_router_dev.py -v
python -m pytest deploy/orchestrator/tests/ 2>&1 | tail -3
```

Expected: all integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add deploy/orchestrator/flyn_orchestrator/dev_phase.py \
        deploy/orchestrator/flyn_orchestrator/router.py
git commit -m "refactor(orchestrator): extract dev_phase.py from router

Phase 2c T05. _run_dev_pr_phase and the dev branch of handle_approval
move to dev_phase.{run_pr_phase, handle_approval}. _format_pr_body
moves with them. Router loses ~150 lines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Cache `_services` on TaskRouter + final verification

**Files:**
- Modify: `deploy/orchestrator/flyn_orchestrator/router.py`

- [ ] **Step 1: Build services bundle once in `__init__`**

In `TaskRouter.__init__`, after all field assignments, add:

```python
        from .phase_services import PhaseServices
        self._services = PhaseServices(
            store=self._store,
            memory=self._memory,
            channels=self._channels,
            reviewer_invoker=self._reviewer_invoker,
            transition=self._transition,
            safe_transition=self._safe_transition,
            notify=self._notify_originating_channel,
            backend_registry=self._dispatcher._registry,
            scratch_root=Path(self._wt_mgr._dir),
            repo_path_for_workflow=self._repo_path_for_workflow,
            workflows_dir=Path(__file__).parent / "workflows",
        )
```

- [ ] **Step 2: Replace `self._make_services()` call sites with `self._services`**

Find every `self._make_services()` in `router.py` and replace with `self._services`. Delete the `_make_services` method.

```bash
grep -n "_make_services" deploy/orchestrator/flyn_orchestrator/router.py
```
Expected before edit: 5 hits (4 call sites + the method def). After edit: 0 hits.

- [ ] **Step 3: Verify final router.py line count**

```bash
wc -l deploy/orchestrator/flyn_orchestrator/router.py
```

Expected: under 350 lines (the spec target was ~250; allow some headroom).

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest deploy/orchestrator/tests/ -v 2>&1 | tail -10
```

Expected: 192 passed (190 original + 2 from Task 1).

- [ ] **Step 5: Sanity grep — no leftover phase logic in router.py**

```bash
grep -n "_run_research_phase\|_run_content_phase\|_run_ops_phase\|_run_dev_pr_phase\|_execute_ops_and_finalize\|_handle_ops_approval\|_format_pr_body" deploy/orchestrator/flyn_orchestrator/router.py
```
Expected: zero hits. All extracted symbols live in phase modules now.

- [ ] **Step 6: Commit**

```bash
git add deploy/orchestrator/flyn_orchestrator/router.py
git commit -m "refactor(orchestrator): cache PhaseServices on TaskRouter

Phase 2c T06. Build the services bundle once in __init__ rather than
per-call. Final router.py is the state-machine coordinator and
approval dispatcher, ~250-300 lines. No behavior change.

Final line count: <see commit body>
All 192 tests pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Verification Checklist

After all 6 tasks complete:

- [ ] `wc -l deploy/orchestrator/flyn_orchestrator/router.py` < 350
- [ ] `wc -l deploy/orchestrator/flyn_orchestrator/{research,content,ops,dev}_phase.py` — each < 300
- [ ] `python -m pytest deploy/orchestrator/tests/` — 192 passed
- [ ] `grep -rn "self._run_research_phase\|self._run_content_phase\|self._run_ops_phase\|self._run_dev_pr_phase" deploy/orchestrator/flyn_orchestrator/` — only the delegations in router.py (no orphan call sites)
- [ ] No circular imports: `python -c "from flyn_orchestrator.router import TaskRouter; from flyn_orchestrator import research_phase, content_phase, ops_phase, dev_phase"` exits 0

## Out-of-scope reminders

- No new tests for happy-path behavior — existing integration tests are the contract. Only Task 1 adds tests (for the new dataclass).
- No changes to LLM helper modules (`research.py`, `content.py`, `ops.py`).
- No state-machine changes.
- No public-API changes to `TaskRouter`.
