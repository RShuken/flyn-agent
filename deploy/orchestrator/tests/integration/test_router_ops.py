# tests/integration/test_router_ops.py
"""Integration tests for the ops workflow router branch (Task 5 — Phase 5).

Three tests:
  1. test_low_tier_auto_executes      — "low" risk auto-executes end-to-end → DELIVERABLE_READY
  2. test_high_tier_blocks_for_approval — "high" risk halts at AWAITING_OWNER_APPROVAL
  3. test_critical_tier_owner_only     — "critical" risk rejects teammate, accepts owner

Uses stub WorkerBackend that returns predetermined JSON. No live LLM or filesystem
mutations — all targets are tmp_path-based paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.config import Config
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import (
    ApprovalDecision,
    InboundTaskRequest,
    TaskState,
    WorkerRole,
)
from flyn_orchestrator.workflows import load_workflow
from flyn_orchestrator.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Shared stub builder helpers
# ---------------------------------------------------------------------------


def _make_ops_pm_json(target: str, tier_hint: str = "low") -> str:
    """Return a serialised OpsSpec for stub PM output."""
    return json.dumps({
        "title": f"Test ops action ({tier_hint})",
        "rationale": "integration test",
        "target": target,
        "action": "write new content to target file",
        "preconditions": ["file is readable"],
        "postconditions": ["file content matches new token"],
        "rollback": "restore from backup",
        "dry_run_supported": True,
        "estimated_blast_radius": "scoped to target file only",
        "external_calls": [],
    })


def _make_risk_json(tier: str) -> str:
    """Return a serialised risk assessment for stub classifier output."""
    return json.dumps({"tier": tier, "reason": f"stub risk tier={tier}"})


def _make_dry_run_json() -> str:
    return json.dumps({
        "mode": "dry_run",
        "would_do": ["read target", "write new content"],
        "expected_blast_radius": "scoped",
        "concerns": [],
    })


def _make_execute_json() -> str:
    return json.dumps({
        "mode": "execute",
        "actions_taken": ["wrote new token to target"],
        "errors": [],
        "state_changes_observed": ["file content updated"],
    })


def _make_validate_pass_json() -> str:
    return json.dumps({
        "passed": True,
        "summary": "all postconditions verified",
        "postcondition_results": [{
            "postcondition": "file content matches new token",
            "verified": True,
            "evidence": "after-snapshot shows new content",
            "severity_if_failed": "important",
        }],
    })


def _make_validate_fail_json() -> str:
    return json.dumps({
        "passed": False,
        "summary": "postcondition not verified",
        "postcondition_results": [{
            "postcondition": "file content matches new token",
            "verified": False,
            "evidence": "snapshots unchanged — no mutation occurred",
            "severity_if_failed": "critical",
        }],
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ops_router(tmp_path):
    """Build a TaskRouter wired to an ops workflow with a stub backend.

    The stub backend is parameterised at test time via the `_run` closure
    (callers replace router._dispatcher._registry.get('claude-p').run).
    """
    ops_wf = load_workflow(
        Path(__file__).parents[2] / "flyn_orchestrator" / "workflows" / "ops.yaml"
    )

    # We need a real target file so snapshot_target can hash it.
    target_file = tmp_path / "test-token.txt"
    target_file.write_text("old-token-value")

    def _default_run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path)
        wt.mkdir(parents=True, exist_ok=True)
        cap = wt / f"{spec.worker_id}.jsonl"

        if spec.role == WorkerRole.PM:
            body = _make_ops_pm_json(str(target_file), "low")
            cap.write_text(json.dumps({"type": "result", "result": body}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=body,
            )
        elif spec.role == WorkerRole.CRITIC:
            # Risk classifier
            body = _make_risk_json("low")
            cap.write_text(json.dumps({"type": "result", "result": body}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=body,
            )
        elif spec.role == WorkerRole.EXECUTOR:
            if "dry" in spec.worker_id:
                body = _make_dry_run_json()
            else:
                # Real execute — mutate the file so hashes differ
                target_file.write_text("new-token-value")
                body = _make_execute_json()
            cap.write_text(json.dumps({"type": "result", "result": body}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=body,
            )
        elif spec.role == WorkerRole.VALIDATOR:
            body = _make_validate_pass_json()
            cap.write_text(json.dumps({"type": "result", "result": body}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=body,
            )
        else:
            cap.write_text(json.dumps({"type": "result", "result": "{}"}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary="{}",
            )

    backend = MagicMock()
    backend.name = "claude-p"
    backend.run = _default_run

    dispatcher = WorkerDispatcher()
    dispatcher.register_backend("claude-p", backend)

    http = MagicMock()
    http.post.return_value.status_code = 200
    memory = MemoryEmitter(router_url="http://localhost:8400", http=http)
    store = StateStore(db_path=tmp_path / "state.db")
    wt_mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")

    cfg = Config(
        home=tmp_path,
        workspace=tmp_path,
        port=8300,
        router_url="http://localhost:8400",
        default_backend="claude-p",
        concurrent_tasks_max=4,
        concurrent_workers_max=6,
        owner_identifiers=frozenset({"ryanshuken@gmail.com"}),
    )

    router = TaskRouter(
        store=store,
        dispatcher=dispatcher,
        worktree_mgr=wt_mgr,
        memory=memory,
        repo_path_for_workflow=lambda w: tmp_path,
        builder_prompt_path=Path(__file__).parents[2]
        / "flyn_orchestrator"
        / "prompts"
        / "builder.md",
        workflows=[ops_wf],
        config=cfg,
    )
    return router, store, tmp_path, target_file


# ---------------------------------------------------------------------------
# Test 1: low tier auto-executes
# ---------------------------------------------------------------------------


def test_low_tier_auto_executes(ops_router):
    """A 'low' risk intent auto-executes without owner approval and ends in DELIVERABLE_READY.

    The risk-rules.yaml matches "rotate.*test" → low, and the stub classifier
    agrees (returns low). The pipeline should produce at minimum 4 audit_log rows:
    pre-snapshot, dry-run, post-snapshot, validate.
    """
    router, store, tmp_path, target_file = ops_router

    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryanshuken@gmail.com",
        sender_role="owner",
        # "rotate the test token in sandbox" matches the low-tier rule
        intent="rotate the test token in local sandbox",
        external_message_id="msg-ops-low-1",
        workflow_override="ops",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)

    assert final.state == TaskState.DELIVERABLE_READY, (
        f"Expected DELIVERABLE_READY, got {final.state!r}; "
        f"payload={final.raw_payload}"
    )

    # Audit log must have at least 4 rows: pre-snapshot, dry-run, post-snapshot, validate
    audit_rows = store.list_audit(task_id)
    actions = [r["action"] for r in audit_rows]
    assert "pre-snapshot" in actions, f"Missing pre-snapshot in {actions}"
    assert "dry-run" in actions, f"Missing dry-run in {actions}"
    assert "post-snapshot" in actions, f"Missing post-snapshot in {actions}"
    assert "validate" in actions, f"Missing validate in {actions}"
    assert len(audit_rows) >= 4, f"Expected >= 4 audit rows, got {len(audit_rows)}: {actions}"

    # pre-snapshot must have before_hash set
    pre = next(r for r in audit_rows if r["action"] == "pre-snapshot")
    assert pre["before_hash"] is not None and pre["before_hash"] != ""

    # post-snapshot must have both before_hash and after_hash
    post = next(r for r in audit_rows if r["action"] == "post-snapshot")
    assert post["before_hash"] is not None
    assert post["after_hash"] is not None

    # No AWAITING_OWNER_APPROVAL transition for low tier
    events = store.list_events(task_id)
    state_seq = [e["to_state"] for e in events]
    assert "awaiting_owner_approval" not in state_seq, (
        f"Low tier should not hit awaiting_owner_approval; events={state_seq}"
    )


# ---------------------------------------------------------------------------
# Test 2: high tier blocks for approval
# ---------------------------------------------------------------------------


def test_high_tier_blocks_for_approval(ops_router):
    """A 'high' risk intent halts at AWAITING_OWNER_APPROVAL.

    The stub classifier overrides the rule-based tier to 'high'.
    After run_task the state should be AWAITING_OWNER_APPROVAL and audit_log
    should have pre-snapshot and dry-run rows but NO post-snapshot/validate rows.
    """
    router, store, tmp_path, target_file = ops_router

    # Override classifier to return 'high'
    original_run = router._dispatcher._registry.get("claude-p").run

    def _run_high_tier(spec, prompt, *, cost_tracker=None):
        if spec.role == WorkerRole.CRITIC:
            wt = Path(spec.worktree_path)
            wt.mkdir(parents=True, exist_ok=True)
            cap = wt / f"{spec.worker_id}.jsonl"
            body = _make_risk_json("high")
            cap.write_text(json.dumps({"type": "result", "result": body}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=body,
            )
        return original_run(spec, prompt, cost_tracker=cost_tracker)

    router._dispatcher._registry.get("claude-p").run = _run_high_tier

    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryanshuken@gmail.com",
        sender_role="owner",
        intent="deploy to production environment",  # matches high-tier rule
        external_message_id="msg-ops-high-1",
        workflow_override="ops",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)

    assert final.state == TaskState.AWAITING_OWNER_APPROVAL, (
        f"Expected AWAITING_OWNER_APPROVAL, got {final.state!r}"
    )

    # Audit log: pre-snapshot + dry-run present, but NO post-snapshot or validate
    audit_rows = store.list_audit(task_id)
    actions = [r["action"] for r in audit_rows]
    assert "pre-snapshot" in actions, f"Missing pre-snapshot in {actions}"
    assert "dry-run" in actions, f"Missing dry-run in {actions}"
    assert "post-snapshot" not in actions, (
        f"post-snapshot should not appear before approval; actions={actions}"
    )
    assert "validate" not in actions, (
        f"validate should not appear before approval; actions={actions}"
    )

    # The task payload should include approval_context with risk_tier
    payload = final.raw_payload or {}
    assert payload.get("risk_tier") == "high"
    assert "approval_context" in payload
    assert payload["approval_context"].get("requires_rationale") is not True  # only critical needs it


# ---------------------------------------------------------------------------
# Test 3: critical tier — teammate rejected, owner accepted
# ---------------------------------------------------------------------------


def test_critical_tier_owner_only(ops_router):
    """A 'critical' risk intent rejects teammate approval and accepts owner approval.

    Steps:
    1. Submit an intent that maps to 'critical' tier.
    2. run_task → AWAITING_OWNER_APPROVAL.
    3. Attempt teammate approval → PermissionError raised.
    4. Submit owner approval with rationale → task proceeds to DELIVERABLE_READY.
    """
    router, store, tmp_path, target_file = ops_router

    # Override classifier to return 'critical'
    original_run = router._dispatcher._registry.get("claude-p").run

    def _run_critical_tier(spec, prompt, *, cost_tracker=None):
        if spec.role == WorkerRole.CRITIC:
            wt = Path(spec.worktree_path)
            wt.mkdir(parents=True, exist_ok=True)
            cap = wt / f"{spec.worker_id}.jsonl"
            body = _make_risk_json("critical")
            cap.write_text(json.dumps({"type": "result", "result": body}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=body,
            )
        return original_run(spec, prompt, cost_tracker=cost_tracker)

    router._dispatcher._registry.get("claude-p").run = _run_critical_tier

    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryanshuken@gmail.com",
        sender_role="owner",
        intent="delete the production database backup",  # matches critical rule
        external_message_id="msg-ops-critical-1",
        workflow_override="ops",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)

    assert final.state == TaskState.AWAITING_OWNER_APPROVAL, (
        f"Expected AWAITING_OWNER_APPROVAL after critical intent, got {final.state!r}"
    )

    # 3. Teammate (Eric) approval must be rejected. eric@example.com is NOT in
    # owner_identifiers, so approver_role resolves to "teammate"; critical tier
    # rejects regardless of what gate value the caller sends.
    with pytest.raises(PermissionError, match="owner"):
        router.handle_approval(
            task_id,
            ApprovalDecision(
                task_id=task_id,
                gate="critical",  # sending "critical" here must NOT grant owner role
                approver="eric@example.com",
                approved=True,
                reason="Eric thinks it is fine",
            ),
        )

    # 4. Owner (Ryan) without rationale must also be rejected. ryanshuken@gmail.com
    # IS in owner_identifiers so role=owner, but empty reason triggers ValueError.
    with pytest.raises(ValueError, match="rationale"):
        router.handle_approval(
            task_id,
            ApprovalDecision(
                task_id=task_id,
                gate="critical",
                approver="ryanshuken@gmail.com",
                approved=True,
                reason="",   # empty — should fail
            ),
        )

    # 5. Owner (Ryan) with rationale — should succeed.
    final2 = router.handle_approval(
        task_id,
        ApprovalDecision(
            task_id=task_id,
            gate="critical",
            approver="ryanshuken@gmail.com",
            approved=True,
            reason="Confirmed we need to drop this corrupted backup; rollback tested.",
        ),
    )

    assert final2.state == TaskState.DELIVERABLE_READY, (
        f"Expected DELIVERABLE_READY after owner approval, got {final2.state!r}"
    )

    # Audit log should now include an "approved" row with actor=ryanshuken@gmail.com
    audit_rows = store.list_audit(task_id)
    approved_rows = [r for r in audit_rows if r["action"] == "approved"]
    assert approved_rows, f"No 'approved' audit row found; rows={[r['action'] for r in audit_rows]}"
    assert approved_rows[0]["actor"] == "ryanshuken@gmail.com"

    # All 4 pipeline rows present after approval resume
    actions = [r["action"] for r in audit_rows]
    assert "pre-snapshot" in actions
    assert "dry-run" in actions
    assert "post-snapshot" in actions
    assert "validate" in actions
