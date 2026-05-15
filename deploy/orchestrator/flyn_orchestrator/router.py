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

from .adapters import ChannelRegistry
from .cost import BudgetExceeded, CostTracker
from .dispatcher import WorkerDispatcher, WorkerProducedNothing
from .memory import MemoryEmitter
from .pr import PRError
from .research import (
    decompose_intent, run_researchers, critique, synthesize, write_output,
)
from .reviewer import review as _default_review
from .state import StateStore
from .workflows import Workflow, match_intent
from .types import (
    ApprovalDecision,
    InboundTaskRequest,
    ReviewFindings,
    TaskRecord,
    TaskState,
    WorkerRole,
    WorkerSpec,
)
from .worktree import WorktreeManager


def _format_pr_body(task: TaskRecord, plan: dict, review: ReviewFindings) -> str:
    icon = "✅" if review.passed else "⚠️"
    findings_md = "\n".join(
        f"- {'🔴' if f.severity == 'critical' else '🟡' if f.severity == 'important' else '🔵'} "
        f"**{f.severity}/{f.area}:** {f.note}"
        for f in review.findings
    ) or "_No findings._"
    files_md = "\n".join(f"- `{f}`" for f in plan.get("estimated_files_touched", []))
    return f"""## {icon} {plan.get('title', task.intent[:60])}

**Task ID:** {task.task_id}
**Requester:** {task.sender_identifier} ({task.sender_role})

### Rationale
{plan.get('rationale', '(none)')}

### Files touched
{files_md or '(none listed)'}

### Reviewer verdict
{review.summary}

### Findings
{findings_md}

### Verification
{plan.get('verification', '(none)')}

---
🤖 Built by Flyn (orchestrator). Builder prompt: see `~/.flyn/orchestrator/workspaces/{task.task_id}/`.
"""


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
        channel_registry: Optional[ChannelRegistry] = None,
        workflows: Optional[list[Workflow]] = None,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._wt_mgr = worktree_mgr
        self._memory = memory
        self._repo_path_for_workflow = repo_path_for_workflow
        self._builder_prompt_path = builder_prompt_path
        self._reviewer_invoker = reviewer_invoker or _default_review
        self._channels = channel_registry
        self._workflows = workflows or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def accept(self, req: InboundTaskRequest) -> str:
        """Insert task into the store and return its task_id immediately."""
        task_id = self._store.next_task_id()
        matched = match_intent(req.intent, self._workflows)
        workflow_name = matched.name if matched else "default"
        workflow = req.workflow_override or workflow_name
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

            # Research workflow branches here — skips builder/reviewer/PR phases.
            if t.workflow == "research":
                self._run_research_phase(t)
                return self._store.get_task(task_id)

            # Synthesised plan object (Phase 2 MVP — real PM invocation is Phase 2b)
            plan_obj = {
                "title": t.intent[:60],
                "rationale": "Generated from intent during dispatch.",
                "builder_brief": t.intent,
                "estimated_files_touched": [],
                "verification": "Reviewer verified.",
            }

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

            # 13. Branch on workflow: dev gets PR phase; default goes straight to DELIVERABLE_READY
            if t.workflow == "dev":
                return self._run_dev_pr_phase(
                    task_id=task_id,
                    task=t,
                    plan_obj=plan_obj,
                    findings=findings,
                    worktree_path=worktree_path,
                    repo_path=repo_path,
                )
            else:
                self._transition(task_id, current_state, TaskState.DELIVERABLE_READY,
                                 actor="router", reason="review complete")
                current_state = TaskState.DELIVERABLE_READY

                self._memory.emit(
                    source="orchestrator",
                    event_type="task_completed",
                    subject=task_id,
                    body=f"Task {task_id} completed and deliverable ready.",
                    dedup_key=f"orch-{task_id}-completed",
                    importance="warm",
                )

                final_task = self._store.get_task(task_id)
                self._notify_originating_channel(final_task, findings)
                return final_task

        except BudgetExceeded:
            self._safe_transition(task_id, current_state, TaskState.COST_PAUSED,
                                  actor="router", reason="budget exceeded")
            raise

        except WorkerProducedNothing as ex:
            self._safe_transition(task_id, current_state, TaskState.FAILED,
                                  actor="dispatcher", reason=str(ex)[:200])
            self._memory.emit(
                source="orchestrator",
                event_type="task_failed",
                subject=task_id,
                body=f"Worker silent failure: {ex}",
                dedup_key=f"orch-{task_id}-silent-failure",
                importance="warm",
            )
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
    # Dev-workflow PR phase
    # ------------------------------------------------------------------

    def _run_dev_pr_phase(
        self,
        *,
        task_id: str,
        task: TaskRecord,
        plan_obj: dict,
        findings: ReviewFindings,
        worktree_path: Path,
        repo_path: Path,
    ) -> TaskRecord:
        """Push branch, open PR, transition to FINAL_APPROVAL_PENDING.

        Falls back to DELIVERABLE_READY (Phase 1 MVP terminal) on push or
        PR-create failure so a dev workflow without origin auth still ships.
        """
        branch = f"flyn/{task_id}"

        # --- 1. Push branch ---
        try:
            subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            self._memory.emit(
                source="orchestrator",
                event_type="pr_push_failed",
                subject=task_id,
                body=f"git push failed: {(e.stderr or '').strip()[:200]}",
                dedup_key=f"orch-{task_id}-push-fail",
                importance="warm",
            )
            self._safe_transition(task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
                                  actor="router", reason="push failed; falling back to deliverable_ready")
            self._notify_originating_channel(self._store.get_task(task_id), findings)
            return self._store.get_task(task_id)

        # --- 2. Create PR ---
        body = _format_pr_body(task, plan_obj, findings)
        title = (plan_obj or {}).get("title") or task.intent[:60]
        try:
            from .pr import create_pr
            pr_url = create_pr(
                repo_path=Path(repo_path),
                title=title,
                body=body,
                base="main",
                head=branch,
            )
        except PRError as e:
            self._memory.emit(
                source="orchestrator",
                event_type="pr_create_failed",
                subject=task_id,
                body=f"gh pr create failed: {str(e)[:200]}",
                dedup_key=f"orch-{task_id}-pr-fail",
                importance="warm",
            )
            self._safe_transition(task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
                                  actor="router", reason="PR create failed; falling back to deliverable_ready")
            self._notify_originating_channel(self._store.get_task(task_id), findings)
            return self._store.get_task(task_id)

        # --- 3. Store PR metadata, transition, notify ---
        self._store.update_task_payload(task_id, {"pr_url": pr_url, "branch": branch})
        self._safe_transition(task_id, TaskState.REVIEWED, TaskState.FINAL_APPROVAL_PENDING,
                              actor="router", reason=f"PR {pr_url} opened")
        self._memory.emit(
            source="orchestrator",
            event_type="pr_opened",
            subject=task_id,
            body=f"PR opened: {pr_url}",
            dedup_key=f"orch-{task_id}-pr",
            importance="warm",
        )
        # Re-fetch so notify has the updated payload (pr_url, branch)
        updated_task = self._store.get_task(task_id)
        self._notify_originating_channel(updated_task, findings, pr_url=pr_url)
        return updated_task

    # ------------------------------------------------------------------
    # Research-workflow phase
    # ------------------------------------------------------------------

    def _run_research_phase(self, task: TaskRecord) -> None:
        """Walk the 5-step research flow. Idempotent for state transitions.

        Steps:
          DECOMPOSED → DISPATCHED (PM decomposes intent)
          DISPATCHED → RUNNING   (parallel researchers)
          RUNNING    → REVIEWED  (critique)
          REVIEWED   → DELIVERABLE_READY | CHANGES_REQUESTED
        """
        backend = self._dispatcher._registry.get("claude-p")
        scratch = self._wt_mgr._dir / task.task_id
        scratch.mkdir(parents=True, exist_ok=True)

        # 1. Decompose
        self._safe_transition(
            task.task_id, TaskState.DECOMPOSED, TaskState.DISPATCHED,
            actor="router", reason="research: PM decomposing",
        )
        plan = decompose_intent(
            task.intent, scratch_dir=scratch, backend=backend, task_id=task.task_id,
        )
        if plan is None or not plan.sub_questions:
            self._safe_transition(
                task.task_id, TaskState.DISPATCHED, TaskState.FAILED,
                actor="research", reason="PM output unparseable or empty",
            )
            self._memory.emit(
                source="orchestrator", event_type="task_failed",
                subject=task.task_id, body="research PM step failed",
                dedup_key=f"orch-{task.task_id}-pm-fail", importance="warm",
            )
            return

        # 2. Researchers
        self._safe_transition(
            task.task_id, TaskState.DISPATCHED, TaskState.RUNNING,
            actor="research", reason=f"running {len(plan.sub_questions)} researchers",
        )
        outputs = run_researchers(
            plan, scratch_dir=scratch, backend=backend,
            task_id=task.task_id, max_parallel=4,
        )
        if not outputs:
            self._safe_transition(
                task.task_id, TaskState.RUNNING, TaskState.FAILED,
                actor="research", reason="no researcher outputs",
            )
            return

        # 3. Critique
        self._safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.REVIEWED,
            actor="research", reason=f"got {len(outputs)} researcher outputs",
        )
        critique_result = critique(
            plan, outputs, scratch_dir=scratch, backend=backend, task_id=task.task_id,
        )
        self._memory.emit(
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
            self._safe_transition(
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
        self._store.update_task_payload(task.task_id, {
            "report_path": str(report_path),
            "research_title": plan.title,
        })
        self._safe_transition(
            task.task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
            actor="router", reason=f"report at {report_path}",
        )
        self._memory.emit(
            source="orchestrator", event_type="research_complete",
            subject=task.task_id,
            body=f"Research report '{plan.title}' delivered to {report_path}",
            dedup_key=f"orch-{task.task_id}-research", importance="warm",
        )
        self._notify_originating_channel(
            self._store.get_task(task.task_id), None,
            research_report_path=str(report_path),
            research_summary=report_md[:1500],
        )

    # ------------------------------------------------------------------
    # Approval handling
    # ------------------------------------------------------------------

    def handle_approval(self, task_id: str, decision: ApprovalDecision) -> TaskRecord:
        """Process an approval decision for a task.

        For dev workflow at FINAL_APPROVAL_PENDING:
          - approved=True  → merge PR → COMPLETED (or FAILED if merge fails)
          - approved=False → CANCELLED

        For other states/workflows: raises NotImplementedError.
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")

        if task.state == TaskState.FINAL_APPROVAL_PENDING and task.workflow == "dev":
            if not decision.approved:
                self._safe_transition(
                    task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.CANCELLED,
                    actor=decision.approver,
                    reason=decision.reason or "rejected",
                )
                return self._store.get_task(task_id)

            # Merge the PR
            pr_url = (task.raw_payload or {}).get("pr_url")
            if pr_url:
                from .pr import merge_pr, pr_number_from_url
                try:
                    pr_num = pr_number_from_url(pr_url)
                    repo_path = self._repo_path_for_workflow(task.workflow)
                    merged = merge_pr(repo_path=Path(repo_path), pr_number=pr_num)
                except Exception:
                    merged = False

                if merged:
                    self._safe_transition(
                        task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.COMPLETED,
                        actor=decision.approver,
                        reason=f"PR #{pr_num} merged",
                    )
                    self._memory.emit(
                        source="orchestrator",
                        event_type="pr_merged",
                        subject=task_id,
                        body=f"PR #{pr_num} merged",
                        dedup_key=f"orch-{task_id}-merged",
                        importance="warm",
                    )
                else:
                    self._safe_transition(
                        task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.FAILED,
                        actor=decision.approver,
                        reason="merge failed",
                    )
            else:
                # No PR URL stored (fallback path that still ended up at FINAL_APPROVAL_PENDING)
                self._safe_transition(
                    task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.COMPLETED,
                    actor=decision.approver,
                    reason="approved (no PR; MVP fallback)",
                )
            return self._store.get_task(task_id)

        raise NotImplementedError(
            f"approval for task {task_id!r} in state {task.state!r} "
            f"workflow={task.workflow!r} not implemented"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify_originating_channel(
        self,
        task: TaskRecord,
        review: Optional[ReviewFindings],
        *,
        pr_url: Optional[str] = None,
        research_report_path: Optional[str] = None,
        research_summary: Optional[str] = None,
    ) -> None:
        """Best-effort notification to the originating channel. Never raises."""
        if self._channels is None:
            return
        payload = task.raw_payload or {}
        channel_name = payload.get("channel")
        chat_id = payload.get("chat_id")
        if not channel_name:
            return
        try:
            adapter = self._channels.get(channel_name)
        except KeyError:
            return
        body = self._format_notify_body(
            task, review,
            pr_url=pr_url,
            research_report_path=research_report_path,
            research_summary=research_summary,
        )
        target = str(chat_id) if chat_id else task.sender_identifier
        try:
            adapter.send(channel=target, body=body)
        except Exception:
            return  # best-effort

    def _format_notify_body(
        self,
        task: TaskRecord,
        review: Optional[ReviewFindings],
        *,
        pr_url: Optional[str] = None,
        research_report_path: Optional[str] = None,
        research_summary: Optional[str] = None,
    ) -> str:
        intent_short = (task.intent or "")[:200]
        if research_report_path:
            # Research workflow notification
            summary_short = (research_summary or "")[:500]
            if research_summary and len(research_summary) > 500:
                summary_short += "..."
            report_line = f"\n*Report:* `{research_report_path}`"
            summary_line = f"\n*Summary:*\n{summary_short}" if summary_short else ""
            return (
                f"✅ *{task.task_id} research delivered*\n"
                f"\n"
                f"*Intent:* {intent_short}"
                f"{report_line}"
                f"{summary_line}"
            )
        # Default / dev workflow notification
        n_findings = len(review.findings) if review else 0
        n_critical = sum(1 for f in review.findings if f.severity == "critical") if review else 0
        summary_short = (review.summary or "")[:200] if review else ""
        icon = "✅" if (review and review.passed) else "⚠️"
        pr_line = f"\n*PR:* {pr_url}" if pr_url else ""
        return (
            f"{icon} *{task.task_id} delivered*\n"
            f"\n"
            f"*Intent:* {intent_short}\n"
            f"*Verdict:* {summary_short}\n"
            f"*Findings:* {n_findings} ({n_critical} critical)\n"
            f"*Capture:* ~/.flyn/orchestrator/workspaces/{task.task_id}/"
            f"{pr_line}"
        )

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
