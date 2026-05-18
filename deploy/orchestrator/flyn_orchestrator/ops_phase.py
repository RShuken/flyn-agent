# deploy/orchestrator/flyn_orchestrator/ops_phase.py
"""Ops-workflow phase runner.

Walks the ops workflow's risk-tier-gated pipeline with audit log:
  DECOMPOSED → DISPATCHED  (PM specs the action)
  DISPATCHED → RUNNING     (classify risk + dry-run)
  RUNNING → AWAITING_OWNER_APPROVAL  (medium/high/critical tier)
  RUNNING → DELIVERABLE_READY        (low tier auto-executes)

Critical-tier requires owner + written rationale; medium/high allow
owner-or-teammate. Low tier auto-executes. One-way escalation enforced
in ops.classify_risk via max_tier().
"""
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import audit as _audit
from . import ops as _ops
from .risk_tier import load_rules
from .types import ApprovalDecision, TaskRecord, TaskState

if TYPE_CHECKING:
    from .phase_services import PhaseServices


# Auth tier sets — kept module-private; they are the contract of the
# auth check inside _handle_approval_impl.
_OWNER_ROLES = frozenset({"owner"})
_TEAMMATE_OR_OWNER_ROLES = frozenset({"owner", "teammate"})


def run(task: TaskRecord, services: "PhaseServices") -> None:
    """Walk the full ops pipeline.

    Steps:
      DECOMPOSED → DISPATCHED  (PM specs the action)
      DISPATCHED → RUNNING     (classify risk + dry-run)
      RUNNING → AWAITING_OWNER_APPROVAL  (medium/high/critical tier)
      RUNNING → DELIVERABLE_READY        (low tier auto-executes)

    After execution (either auto or resumed from approval):
      validate → DELIVERABLE_READY or AWAITING_OWNER_APPROVAL (validator concerns)
    """
    backend = services.backend_registry.get("claude-p")
    scratch = services.scratch_root / task.task_id
    scratch.mkdir(parents=True, exist_ok=True)

    # 1. Spec (PM)
    services.safe_transition(
        task.task_id, TaskState.DECOMPOSED, TaskState.DISPATCHED,
        actor="ops", reason="PM speccing ops action",
    )
    spec = _ops.spec_ops_action(
        task.intent, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    if spec is None or spec.title.startswith("("):
        services.safe_transition(
            task.task_id, TaskState.DISPATCHED, TaskState.FAILED,
            actor="ops", reason="PM ops spec unparseable or ambiguous",
        )
        services.memory.emit(
            source="orchestrator", event_type="task_failed",
            subject=task.task_id, body="ops PM step failed",
            dedup_key=f"orch-{task.task_id}-pm-fail", importance="warm",
        )
        return

    # Persist OpsSpec to task state
    services.store.update_task_payload(task.task_id, {
        "ops_spec": {
            "title": spec.title,
            "rationale": spec.rationale,
            "target": spec.target,
            "action": spec.action,
            "preconditions": spec.preconditions,
            "postconditions": spec.postconditions,
            "rollback": spec.rollback,
            "dry_run_supported": spec.dry_run_supported,
            "estimated_blast_radius": spec.estimated_blast_radius,
            "external_calls": spec.external_calls,
        }
    })

    # 2. Classify risk
    services.safe_transition(
        task.task_id, TaskState.DISPATCHED, TaskState.RUNNING,
        actor="ops", reason="risk classify + dry-run",
    )
    rules_path = services.workflows_dir / "ops" / "risk-rules.yaml"
    rules = load_rules(rules_path)
    risk = _ops.classify_risk(
        task.intent, spec,
        rules=rules,
        scratch_dir=scratch,
        backend=backend,
        task_id=task.task_id,
    )
    tier = risk.tier

    # Persist risk assessment
    services.store.update_task_payload(task.task_id, {
        "risk_tier": tier,
        "risk_reason": risk.reason,
        "risk_upgraded_from_rule": risk.upgraded_from_rule,
        "risk_rule_floor": risk.rule_floor,
    })

    # 3. Pre-snapshot
    before_snap = _audit.snapshot_target(spec.target)
    services.store.append_audit(
        task_id=task.task_id,
        actor="ops",
        action="pre-snapshot",
        target=spec.target,
        before_hash=before_snap.hash_value or None,
        after_hash=None,
        payload={"tier": tier, "kind": before_snap.kind},
    )

    # 4. Dry-run
    dry_result = _ops.dry_run_action(
        spec, tier=tier, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    services.store.append_audit(
        task_id=task.task_id,
        actor="ops",
        action="dry-run",
        target=spec.target,
        before_hash=before_snap.hash_value or None,
        after_hash=None,
        payload={
            "tier": tier,
            "would_do": dry_result.would_do,
            "concerns": dry_result.concerns,
            "expected_blast_radius": dry_result.expected_blast_radius,
        },
    )

    # 5. Tier-based gate
    if tier == "low":
        # Auto-approve: execute immediately
        execute_and_finalize(
            task=task,
            spec=spec,
            tier=tier,
            before_snap=before_snap,
            scratch=scratch,
            backend=backend,
            services=services,
        )
    else:
        # medium / high / critical — block for owner approval
        approval_context: dict = {
            "ops_spec_title": spec.title,
            "risk_tier": tier,
            "risk_reason": risk.reason,
            "dry_run_would_do": dry_result.would_do,
            "dry_run_concerns": dry_result.concerns,
        }
        if tier == "critical":
            approval_context["requires_rationale"] = True

        services.store.update_task_payload(task.task_id, {
            "approval_context": approval_context,
        })
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.AWAITING_OWNER_APPROVAL,
            actor="ops",
            reason=f"risk tier={tier}; awaiting owner approval",
        )
        services.memory.emit(
            source="orchestrator", event_type="ops_awaiting_approval",
            subject=task.task_id,
            body=f"Ops task {task.task_id} blocked at tier={tier}; awaiting owner approval",
            dedup_key=f"orch-{task.task_id}-awaiting", importance="warm",
        )


def execute_and_finalize(
    task: TaskRecord,
    *,
    spec: "_ops.OpsSpec",
    tier: str,
    before_snap: "_audit.SnapshotBundle",
    scratch: Path,
    backend,
    services: "PhaseServices",
) -> None:
    """Execute the ops action, post-snapshot, validate, and transition to final state.

    Called either directly (low tier) or after owner approval (medium/high/critical).
    """
    # Execute
    exec_result = _ops.execute_action(
        spec, tier=tier, scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )

    # Post-snapshot
    after_snap = _audit.snapshot_target(spec.target)
    changed = _audit.verify_target_changed(before_snap, after_snap)
    services.store.append_audit(
        task_id=task.task_id,
        actor="ops",
        action="post-snapshot",
        target=spec.target,
        before_hash=before_snap.hash_value or None,
        after_hash=after_snap.hash_value or None,
        payload={
            "tier": tier,
            "changed": changed,
            "actions_taken": exec_result.actions_taken,
            "errors": exec_result.errors,
        },
    )

    # Validate
    val_result = _ops.validate_action(
        spec, before_snap, after_snap,
        scratch_dir=scratch, backend=backend, task_id=task.task_id,
    )
    services.store.append_audit(
        task_id=task.task_id,
        actor="ops",
        action="validate",
        target=spec.target,
        before_hash=before_snap.hash_value or None,
        after_hash=after_snap.hash_value or None,
        payload={
            "passed": val_result.passed,
            "summary": val_result.summary,
            "tier": tier,
        },
    )

    if val_result.passed:
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.DELIVERABLE_READY,
            actor="ops", reason=f"validator passed; tier={tier}",
        )
        services.memory.emit(
            source="orchestrator", event_type="ops_complete",
            subject=task.task_id,
            body=f"Ops task {task.task_id} completed successfully; tier={tier}",
            dedup_key=f"orch-{task.task_id}-ops-complete", importance="warm",
        )
    else:
        # Validator concerns — block for owner review even if low tier auto-executed
        services.store.update_task_payload(task.task_id, {
            "validator_concerns": val_result.summary,
            "validator_postcondition_results": [
                {
                    "postcondition": p.postcondition,
                    "verified": p.verified,
                    "evidence": p.evidence,
                    "severity_if_failed": p.severity_if_failed,
                }
                for p in val_result.postcondition_results
            ],
        })
        services.safe_transition(
            task.task_id, TaskState.RUNNING, TaskState.AWAITING_OWNER_APPROVAL,
            actor="validator",
            reason=f"validator FAIL; tier={tier}; concerns: {val_result.summary[:200]}",
        )
        services.memory.emit(
            source="orchestrator", event_type="ops_validator_fail",
            subject=task.task_id,
            body=f"Validator failed for {task.task_id}: {val_result.summary}",
            dedup_key=f"orch-{task.task_id}-val-fail", importance="warm",
        )


def handle_approval(
    task: TaskRecord,
    decision: ApprovalDecision,
    services: "PhaseServices",
) -> TaskRecord:
    """Handle AWAITING_OWNER_APPROVAL for ops: enforce auth + resume or reject.

    Resolves approver_role from the configured owner_identifiers set: if the
    approver's identifier appears in services.config.owner_identifiers they
    receive role "owner"; otherwise "teammate".  The decision.gate field is
    the REQUIRED approval level for the task, not the caller's role — using it
    to infer role would allow any caller to self-escalate.
    """
    owner_ids = (
        services.config.owner_identifiers
        if services.config is not None
        else frozenset()
    )
    approver_role = "owner" if decision.approver in owner_ids else "teammate"
    decision_str = "approve" if decision.approved else "reject"
    rationale = decision.reason

    return _handle_approval_impl(
        task=task,
        approver=decision.approver,
        decision=decision_str,
        approver_role=approver_role,
        rationale=rationale,
        services=services,
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
    """Handle human approval for an ops task at AWAITING_OWNER_APPROVAL.

    decision: "approve" | "reject"
    approver_role: "owner" | "teammate" (caller must supply from auth context)

    Auth rules:
      - critical tier: owner only
      - medium/high tier: owner or teammate

    Critical tier additionally requires a non-empty rationale string.
    """
    payload = task.raw_payload or {}
    tier = payload.get("risk_tier", "medium")

    # Auth check
    if tier == "critical":
        if approver_role not in _OWNER_ROLES:
            raise PermissionError(
                f"critical-tier ops task {task.task_id!r} requires owner approval; "
                f"approver {approver!r} has role {approver_role!r}"
            )
        if decision == "approve" and not rationale:
            raise ValueError(
                f"critical-tier approval for {task.task_id!r} requires an explicit rationale"
            )
    else:
        # medium or high — owner or teammate
        if approver_role not in _TEAMMATE_OR_OWNER_ROLES:
            raise PermissionError(
                f"ops task {task.task_id!r} (tier={tier}) requires owner or teammate approval; "
                f"approver {approver!r} has role {approver_role!r}"
            )

    if decision == "reject":
        services.store.append_audit(
            task_id=task.task_id,
            actor=approver,
            action="reject",
            target=payload.get("ops_spec", {}).get("target", ""),
            before_hash=None,
            after_hash=None,
            payload={"tier": tier, "rationale": rationale or ""},
        )
        services.safe_transition(
            task.task_id, TaskState.AWAITING_OWNER_APPROVAL, TaskState.REJECTED,
            actor=approver, reason=f"ops rejected by {approver}; tier={tier}",
        )
        return services.store.get_task(task.task_id)

    # Approved — resume execute phase
    # Rebuild OpsSpec from stored payload
    spec_data = payload.get("ops_spec") or {}
    spec = _ops.OpsSpec(
        title=spec_data.get("title", ""),
        rationale=spec_data.get("rationale", ""),
        target=spec_data.get("target", ""),
        action=spec_data.get("action", ""),
        preconditions=list(spec_data.get("preconditions") or []),
        postconditions=list(spec_data.get("postconditions") or []),
        rollback=spec_data.get("rollback", ""),
        dry_run_supported=bool(spec_data.get("dry_run_supported", False)),
        estimated_blast_radius=spec_data.get("estimated_blast_radius", ""),
        external_calls=list(spec_data.get("external_calls") or []),
    )

    # Re-take before snapshot (task was paused; re-snapshot current state)
    before_snap = _audit.snapshot_target(spec.target)

    services.store.append_audit(
        task_id=task.task_id,
        actor=approver,
        action="approved",
        target=spec.target,
        before_hash=before_snap.hash_value or None,
        after_hash=None,
        payload={"tier": tier, "rationale": rationale or ""},
    )

    # Transition back to RUNNING for execution
    services.safe_transition(
        task.task_id, TaskState.AWAITING_OWNER_APPROVAL, TaskState.RUNNING,
        actor=approver, reason=f"approved by {approver}; tier={tier}",
    )

    backend = services.backend_registry.get("claude-p")
    scratch = services.scratch_root / task.task_id
    scratch.mkdir(parents=True, exist_ok=True)

    execute_and_finalize(
        task=task,
        spec=spec,
        tier=tier,
        before_snap=before_snap,
        scratch=scratch,
        backend=backend,
        services=services,
    )

    return services.store.get_task(task.task_id)
