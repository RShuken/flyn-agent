"""Dev-workflow PR phase runner.

After the main builder/reviewer flow lands at REVIEWED, dev workflow pushes
the branch and opens a PR. On approval, the PR merges and the task transitions
to COMPLETED.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from . import pr as _pr
from .pr import PRError
from .types import ApprovalDecision, ReviewFindings, TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


def _format_pr_body(task: TaskRecord, plan: dict, review: ReviewFindings) -> str:
    """Render PR body with task metadata + reviewer findings."""
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

    Falls back to DELIVERABLE_READY on push or PR-create failure so a dev
    workflow without origin auth still ships.
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
        services.memory.emit(
            source="orchestrator",
            event_type="pr_push_failed",
            subject=task_id,
            body=f"git push failed: {(e.stderr or '').strip()[:200]}",
            dedup_key=f"orch-{task_id}-push-fail",
            importance="warm",
        )
        services.safe_transition(
            task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
            actor="router", reason="push failed; falling back to deliverable_ready",
        )
        services.notify(services.store.get_task(task_id), findings)
        return services.store.get_task(task_id)

    # --- 2. Create PR ---
    body = _format_pr_body(task, plan_obj, findings)
    title = (plan_obj or {}).get("title") or task.intent[:60]
    try:
        pr_url = _pr.create_pr(
            repo_path=Path(repo_path),
            title=title,
            body=body,
            base="main",
            head=branch,
        )
    except PRError as e:
        services.memory.emit(
            source="orchestrator",
            event_type="pr_create_failed",
            subject=task_id,
            body=f"gh pr create failed: {str(e)[:200]}",
            dedup_key=f"orch-{task_id}-pr-fail",
            importance="warm",
        )
        services.safe_transition(
            task_id, TaskState.REVIEWED, TaskState.DELIVERABLE_READY,
            actor="router", reason="PR create failed; falling back to deliverable_ready",
        )
        services.notify(services.store.get_task(task_id), findings)
        return services.store.get_task(task_id)

    # --- 3. Store PR metadata, transition, notify ---
    services.store.update_task_payload(task_id, {"pr_url": pr_url, "branch": branch})
    services.safe_transition(
        task_id, TaskState.REVIEWED, TaskState.FINAL_APPROVAL_PENDING,
        actor="router", reason=f"PR {pr_url} opened",
    )
    services.memory.emit(
        source="orchestrator",
        event_type="pr_opened",
        subject=task_id,
        body=f"PR opened: {pr_url}",
        dedup_key=f"orch-{task_id}-pr",
        importance="warm",
    )
    # Re-fetch so notify has the updated payload (pr_url, branch)
    updated_task = services.store.get_task(task_id)
    services.notify(updated_task, findings, pr_url=pr_url)
    return updated_task


def handle_approval(
    task: TaskRecord,
    decision: ApprovalDecision,
    services: "PhaseServices",
) -> TaskRecord:
    """Handle FINAL_APPROVAL_PENDING for dev: merge PR or cancel."""
    task_id = task.task_id

    if not decision.approved:
        services.safe_transition(
            task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.CANCELLED,
            actor=decision.approver,
            reason=decision.reason or "rejected",
        )
        return services.store.get_task(task_id)

    # Merge the PR
    pr_url = (task.raw_payload or {}).get("pr_url")
    if pr_url:
        try:
            pr_num = _pr.pr_number_from_url(pr_url)
            repo_path = services.repo_path_for_workflow(task.workflow)
            merged = _pr.merge_pr(repo_path=Path(repo_path), pr_number=pr_num)
        except Exception:
            merged = False

        if merged:
            services.safe_transition(
                task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.COMPLETED,
                actor=decision.approver,
                reason=f"PR #{pr_num} merged",
            )
            services.memory.emit(
                source="orchestrator",
                event_type="pr_merged",
                subject=task_id,
                body=f"PR #{pr_num} merged",
                dedup_key=f"orch-{task_id}-merged",
                importance="warm",
            )
        else:
            services.safe_transition(
                task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.FAILED,
                actor=decision.approver,
                reason="merge failed",
            )
    else:
        # No PR URL stored (fallback path that still ended up at FINAL_APPROVAL_PENDING)
        services.safe_transition(
            task_id, TaskState.FINAL_APPROVAL_PENDING, TaskState.COMPLETED,
            actor=decision.approver,
            reason="approved (no PR; MVP fallback)",
        )
    return services.store.get_task(task_id)
