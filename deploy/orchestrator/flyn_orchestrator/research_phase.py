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
