# deploy/orchestrator/tests/unit/test_types.py
from __future__ import annotations
import pytest
from pydantic import ValidationError
from flyn_orchestrator.types import (
    TaskRecord, TaskState, InboundTaskRequest, WorkerRole, WorkerSpec,
    ReviewFindings, ReviewFinding, ApprovalGate, ApprovalDecision,
)


def test_task_record_minimal():
    t = TaskRecord(
        task_id="T-0042",
        workflow="dev",
        state=TaskState.INBOUND,
        sender_role="teammate",
        sender_identifier="beth@telegram",
        intent="add sponsor tier section",
    )
    assert t.task_id == "T-0042"
    assert t.state == TaskState.INBOUND


def test_inbound_request_rejects_empty_intent():
    with pytest.raises(ValidationError):
        InboundTaskRequest(
            channel="telegram",
            sender_identifier="ryan",
            sender_role="owner",
            intent="",
            external_message_id="msg-1",
        )


def test_task_state_values():
    assert {s.value for s in TaskState} >= {
        "inbound", "triaging", "routed", "decomposed", "plan_pending",
        "dispatched", "running", "reviewed", "deliverable_ready",
        "final_approval_pending", "completed", "cancelled", "failed",
        "timed_out", "changes_requested", "security_review",
    }


def test_worker_spec_validates():
    s = WorkerSpec(
        task_id="T-1", worker_id="w-001", role=WorkerRole.BUILDER,
        backend="claude-p", prompt_template="builder",
        worktree_path="/tmp/T-1", max_turns=10, budget_usd=5.0,
    )
    assert s.role == WorkerRole.BUILDER


def test_review_findings_serialisable():
    rf = ReviewFindings(
        worker_id="w-001",
        passed=True,
        findings=[ReviewFinding(severity="info", area="style", note="LGTM")],
        summary="all good",
    )
    j = rf.model_dump_json()
    assert "all good" in j
