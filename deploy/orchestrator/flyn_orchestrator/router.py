# deploy/orchestrator/flyn_orchestrator/router.py
"""TaskRouter: the central orchestration loop for Phase 1 MVP.

Walks a task from INBOUND through the full state machine:
  INBOUND → TRIAGING → ROUTED → DECOMPOSED → DISPATCHED → RUNNING
  → REVIEWED → DELIVERABLE_READY

On budget overrun: → COST_PAUSED (re-raises BudgetExceeded)
On any other error: → FAILED (re-raises)
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .cost import BudgetExceeded, CostTracker
from .dispatcher import WorkerDispatcher
from .memory import MemoryEmitter
from .reviewer import review as _default_review
from .state import StateStore
from .types import (
    InboundTaskRequest,
    ReviewFindings,
    TaskRecord,
    TaskState,
    WorkerRole,
    WorkerSpec,
)
from .worktree import WorktreeManager


class TaskRouter:
    """Orchestrates a single task end-to-end (synchronous, single-threaded MVP)."""

    def __init__(
        self,
        store: StateStore,
        dispatcher: WorkerDispatcher,
        worktree_mgr: WorktreeManager,
        memory: MemoryEmitter,
        repo_path_for_workflow: Callable[[str], Path],
        builder_prompt_path: Path,
        reviewer_invoker: Optional[Callable[..., ReviewFindings]] = None,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._wt_mgr = worktree_mgr
        self._memory = memory
        self._repo_path_for_workflow = repo_path_for_workflow
        self._builder_prompt_path = builder_prompt_path
        self._reviewer_invoker = reviewer_invoker or _default_review

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def accept(self, req: InboundTaskRequest) -> str:
        """Insert task into the store and return its task_id immediately."""
        task_id = self._store.next_task_id()
        workflow = req.workflow_override or "default"
        record = TaskRecord(
            task_id=task_id,
            workflow=workflow,
            state=TaskState.INBOUND,
            sender_role=req.sender_role,
            sender_identifier=req.sender_identifier,
            intent=req.intent,
            created_at=datetime.now(timezone.utc),
            budget_usd=5.0,
            raw_payload=req.raw_payload,
        )
        self._store.insert_task(record)
        return task_id

    # States that indicate the task has already been fully processed.
    _TERMINAL_STATES = frozenset({
        TaskState.DELIVERABLE_READY,
        TaskState.COMPLETED,
        TaskState.CANCELLED,
        TaskState.FAILED,
        TaskState.TIMED_OUT,
        TaskState.COST_PAUSED,
    })

    def run_task(self, task_id: str) -> TaskRecord:
        """Synchronous happy-path flow. Returns final TaskRecord or raises.

        Idempotent: if the task is already in a terminal state, returns it immediately.
        """
        t = self._store.get_task(task_id)
        if t is None:
            raise ValueError(f"task {task_id!r} not found")

        # Early-return if already in a terminal/complete state (handles re-runs from BackgroundTasks).
        if t.state in self._TERMINAL_STATES:
            return t

        cost_tracker = CostTracker(budget_usd=t.budget_usd)
        current_state = t.state
        worktree_path: Optional[Path] = None

        try:
            # 1. INBOUND → TRIAGING
            self._transition(task_id, current_state, TaskState.TRIAGING,
                             actor="router", reason="auto-route")
            current_state = TaskState.TRIAGING

            # 2. TRIAGING → ROUTED
            self._transition(task_id, current_state, TaskState.ROUTED,
                             actor="router",
                             reason=f"intent matched workflow={t.workflow}")
            current_state = TaskState.ROUTED

            # 3. ROUTED → DECOMPOSED (stub PM: single-builder plan)
            self._transition(task_id, current_state, TaskState.DECOMPOSED,
                             actor="router",
                             reason="stub PM: single-builder plan")
            current_state = TaskState.DECOMPOSED

            self._memory.emit(
                source="orchestrator",
                event_type="task_decomposed",
                subject=task_id,
                body=f"Decomposed task {task_id}: single builder for '{t.intent}'",
                dedup_key=f"orch-{task_id}-decomposed",
                importance="warm",
            )

            # 4. Allocate worktree
            repo_path = self._repo_path_for_workflow(t.workflow)
            worktree_path = self._wt_mgr.allocate(
                repo_path=repo_path,
                task_id=task_id,
                branch=f"flyn/{task_id}",
            )

            # 5. DECOMPOSED → DISPATCHED
            self._transition(task_id, current_state, TaskState.DISPATCHED,
                             actor="router", reason="builder spec ready")
            current_state = TaskState.DISPATCHED

            self._memory.emit(
                source="orchestrator",
                event_type="worker_dispatched",
                subject=task_id,
                body=f"Builder worker dispatched for task {task_id}",
                dedup_key=f"orch-{task_id}-dispatched",
                importance="cool",
            )

            # 6. DISPATCHED → RUNNING
            self._transition(task_id, current_state, TaskState.RUNNING,
                             actor="router", reason="worker starting")
            current_state = TaskState.RUNNING

            # 7. Build WorkerSpec and render prompt
            spec = WorkerSpec(
                task_id=task_id,
                worker_id=f"{task_id}-builder",
                role=WorkerRole.BUILDER,
                backend="claude-p",
                prompt_template="builder",
                worktree_path=str(worktree_path),
                max_turns=10,
                budget_usd=t.budget_usd,
                allowed_tools=["Edit", "Write", "Bash", "Read"],
            )

            prompt = self._render_builder_prompt(
                task=t.intent,
                requirements="Implement the task; commit changes; output a one-line summary.",
            )

            # 8. Dispatch to backend
            result = self._dispatcher.dispatch(spec, prompt)
            cost_tracker.add(result.cost_usd)

            # 9. RUNNING → REVIEWED
            self._transition(task_id, current_state, TaskState.REVIEWED,
                             actor="router", reason=f"worker exited code={result.exit_code}")
            current_state = TaskState.REVIEWED

            self._memory.emit(
                source="orchestrator",
                event_type="worker_exit",
                subject=task_id,
                body=f"Builder exited for {task_id}: exit_code={result.exit_code}, summary={result.summary!r}",
                dedup_key=f"orch-{task_id}-worker-exit",
                importance="cool",
            )

            # 10. Compute diff from worktree
            diff = self._compute_diff(worktree_path)

            # 11. Invoke reviewer
            findings = self._reviewer_invoker(
                worker_id=task_id,
                requirements=t.intent,
                diff=diff,
                test_results="(no tests run)",
                worktree_path=str(worktree_path),
                backend_name="claude-p",
            )

            # 12. Emit review_complete
            critical_count = sum(
                1 for f in findings.findings if f.severity == "critical"
            )
            self._memory.emit(
                source="orchestrator",
                event_type="review_complete",
                subject=task_id,
                body=(
                    f"Review for {task_id}: passed={findings.passed}, "
                    f"critical_findings={critical_count}. {findings.summary}"
                ),
                dedup_key=f"orch-{task_id}-review-complete",
                importance="warm",
            )

            # 13. REVIEWED → DELIVERABLE_READY
            self._transition(task_id, current_state, TaskState.DELIVERABLE_READY,
                             actor="router", reason="review complete")
            current_state = TaskState.DELIVERABLE_READY

            # 14. Emit task_completed
            self._memory.emit(
                source="orchestrator",
                event_type="task_completed",
                subject=task_id,
                body=f"Task {task_id} completed and deliverable ready.",
                dedup_key=f"orch-{task_id}-completed",
                importance="warm",
            )

            return self._store.get_task(task_id)

        except BudgetExceeded:
            self._safe_transition(task_id, current_state, TaskState.COST_PAUSED,
                                  actor="router", reason="budget exceeded")
            raise

        except Exception as exc:
            self._safe_transition(task_id, current_state, TaskState.FAILED,
                                  actor="router", reason=f"error: {type(exc).__name__}")
            self._memory.emit(
                source="orchestrator",
                event_type="task_failed",
                subject=task_id,
                body=f"Task {task_id} failed with {type(exc).__name__}: {exc}",
                dedup_key=f"orch-{task_id}-failed",
                importance="warm",
            )
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transition(
        self,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        actor: str,
        reason: str,
    ) -> None:
        self._store.transition(
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            reason=reason,
        )

    def _safe_transition(
        self,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        actor: str,
        reason: str,
    ) -> None:
        """Best-effort transition; swallows errors so error-handling path never cascades."""
        try:
            self._store.transition(
                task_id=task_id,
                from_state=from_state,
                to_state=to_state,
                actor=actor,
                reason=reason,
            )
        except Exception:
            pass

    def _render_builder_prompt(self, task: str, requirements: str) -> str:
        template = self._builder_prompt_path.read_text()
        return template.replace("{TASK}", task).replace("{REQUIREMENTS}", requirements)

    def _compute_diff(self, worktree_path: Path) -> str:
        """Return git diff output from the worktree (vs HEAD). Empty string on failure."""
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD~1"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout or ""
            # HEAD~1 may not exist (first commit); fall back to diff HEAD
            result2 = subprocess.run(
                ["git", "diff"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result2.stdout or ""
        except Exception:
            return ""
