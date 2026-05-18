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
        # Phase 3b: auto-rerun once with critic findings as additional context.
        # If the retry also fails, we transition to CHANGES_REQUESTED as before.
        blocking_first = [
            f for f in critique_result.findings
            if f.severity in ("critical", "important")
        ]
        retry_context = _build_retry_context(critique_result.findings)
        services.memory.emit(
            source="orchestrator", event_type="research_retry_started",
            subject=task.task_id,
            body=f"first critique failed; auto-retry with {len(blocking_first)} blocking findings as context",
            dedup_key=f"orch-{task.task_id}-research-retry", importance="warm",
        )

        # Cycle back through DISPATCHED → RUNNING for the retry. Distinct
        # `actor` prevents the UNIQUE(task_id, from_state, to_state, actor)
        # task_events constraint from blocking the re-transition.
        services.safe_transition(
            task.task_id, TaskState.REVIEWED, TaskState.DISPATCHED,
            actor="research-retry", reason="auto-rerun with critic findings",
        )
        services.safe_transition(
            task.task_id, TaskState.DISPATCHED, TaskState.RUNNING,
            actor="research-retry", reason="re-running researchers with retry context",
        )
        outputs = run_researchers(
            plan, scratch_dir=scratch, backend=backend,
            task_id=task.task_id, max_parallel=4,
            extra_context=retry_context,
        )
        if not outputs:
            services.safe_transition(
                task.task_id, TaskState.RUNNING, TaskState.FAILED,
                actor="research-retry", reason="no researcher outputs on retry",
            )
            return

        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.REVIEWED,
            actor="research-retry",
            reason=f"got {len(outputs)} retry outputs",
        )
        critique_result = critique(
            plan, outputs, scratch_dir=scratch, backend=backend, task_id=task.task_id,
        )
        services.memory.emit(
            source="orchestrator",
            event_type="research_retry_passed" if critique_result.passed else "research_retry_failed",
            subject=task.task_id,
            body=f"retry critique passed={critique_result.passed}; "
                 f"{len(critique_result.findings)} findings",
            dedup_key=f"orch-{task.task_id}-research-retry-critique", importance="warm",
        )
        if not critique_result.passed:
            blocking_retry = [
                f for f in critique_result.findings
                if f.severity in ("critical", "important")
            ]
            services.store.update_task_payload(task.task_id, {
                "research_retry_count": 1,
                "research_blocking_findings": [
                    {"severity": f.severity, "category": f.category, "note": f.note}
                    for f in blocking_retry
                ],
            })
            services.safe_transition(
                task.task_id, TaskState.REVIEWED, TaskState.CHANGES_REQUESTED,
                actor="critic",
                reason=f"critique failed twice: {len(blocking_retry)} blocking findings",
            )
            return
        # Retry passed — fall through to synthesize/write/notify below.

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


def _build_retry_context(findings) -> str:
    """Format critic findings as additional context for retry researchers.

    Only blocking severities (critical/important) are surfaced — minor/info
    findings would just add noise to the researcher's prompt without changing
    behavior.
    """
    blocking = [f for f in findings if f.severity in ("critical", "important")]
    if not blocking:
        return ""
    lines = ["## Critic findings from previous research run", ""]
    for f in blocking:
        line = f"- **{f.severity} / {f.category}**: {f.note}"
        sq_id = getattr(f, "sub_question_id", None)
        if sq_id:
            line += f" (re: sub-question `{sq_id}`)"
        lines.append(line)
    lines.append("")
    lines.append("Please address these findings in your research output.")
    return "\n".join(lines)
