# deploy/orchestrator/tests/unit/test_phase_services.py
import dataclasses
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from flyn_orchestrator.phase_services import PhaseServices
from flyn_orchestrator.config import Config


def _make_services(owner_identifiers=frozenset()):
    """Helper: build a minimal PhaseServices with a real Config."""
    cfg = Config(
        home=Path("/tmp"),
        workspace=Path("/tmp"),
        port=8300,
        router_url="http://localhost:8400",
        default_backend="claude-p",
        concurrent_tasks_max=4,
        concurrent_workers_max=6,
        owner_identifiers=owner_identifiers,
    )
    backend_registry = MagicMock()
    backend_registry.get.return_value = MagicMock()
    return PhaseServices(
        store=object(),
        memory=object(),
        channels=None,
        reviewer_invoker=lambda **kw: None,
        transition=lambda *a, **kw: None,
        safe_transition=lambda *a, **kw: None,
        notify=lambda *a, **kw: None,
        backend_registry=backend_registry,
        scratch_root=Path("/tmp"),
        repo_path_for_workflow=lambda w: Path("/tmp"),
        workflows_dir=Path("/tmp"),
        config=cfg,
    )


def test_phase_services_is_frozen():
    """Frozen dataclass: mutation raises FrozenInstanceError."""
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
    """The 12-field surface is the contract phase runners depend on."""
    field_names = {f.name for f in dataclasses.fields(PhaseServices)}
    assert field_names == {
        "store", "memory", "channels", "reviewer_invoker",
        "transition", "safe_transition", "notify",
        "backend_registry", "scratch_root", "repo_path_for_workflow",
        "workflows_dir", "config",
    }


def test_phase_services_config_defaults_to_none():
    """config field is optional — existing callers that omit it still work."""
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
    assert svc.config is None


# ---------------------------------------------------------------------------
# handle_approval role-inference tests
# ---------------------------------------------------------------------------

from flyn_orchestrator.ops_phase import handle_approval
from flyn_orchestrator.types import ApprovalDecision, TaskRecord, TaskState
from datetime import datetime, timezone


def _make_task(tier: str) -> TaskRecord:
    return TaskRecord(
        task_id="task-001",
        workflow="ops",
        state=TaskState.AWAITING_OWNER_APPROVAL,
        sender_role="teammate",
        sender_identifier="sender@example.com",
        intent="rotate token",
        created_at=datetime.now(timezone.utc),
        budget_usd=5.0,
        raw_payload={
            "risk_tier": tier,
            "ops_spec": {
                "title": "Rotate token",
                "rationale": "monthly",
                "target": "/tmp/test-token.txt",
                "action": "replace",
                "preconditions": [],
                "postconditions": [],
                "rollback": "restore",
                "dry_run_supported": True,
                "estimated_blast_radius": "scoped",
                "external_calls": [],
            },
        },
    )


def _noop_transition(*a, **kw):
    pass


def _make_audit_store(tier):
    """Stub StateStore that records audit appends and returns a task."""
    store = MagicMock()
    store.append_audit = MagicMock()
    store.get_task = MagicMock(return_value=_make_task(tier))
    return store


def test_owner_in_identifiers_approves_critical():
    """Approver IS in owner_identifiers → role=owner → critical approval succeeds."""
    owner_email = "owner@example.com"
    svc = _make_services(owner_identifiers=frozenset({owner_email}))
    # Replace store + safe_transition with stubs that don't touch disk
    svc = dataclasses.replace(
        svc,
        store=_make_audit_store("critical"),
        safe_transition=_noop_transition,
    )

    decision = ApprovalDecision(
        task_id="task-001",
        gate="critical",
        approver=owner_email,
        approved=True,
        reason="approved after review",
    )
    task = _make_task("critical")

    # Should NOT raise PermissionError
    from unittest.mock import patch
    with patch("flyn_orchestrator.ops_phase.execute_and_finalize"):
        result = handle_approval(task, decision, svc)
    # We got a result (the mocked store.get_task return value)
    assert result is not None


def test_non_owner_cannot_approve_critical():
    """Approver NOT in owner_identifiers → role=teammate → critical-tier raises PermissionError."""
    svc = _make_services(owner_identifiers=frozenset({"owner@example.com"}))
    svc = dataclasses.replace(
        svc,
        store=_make_audit_store("critical"),
        safe_transition=_noop_transition,
    )

    decision = ApprovalDecision(
        task_id="task-001",
        gate="critical",   # caller claims critical gate — must NOT grant owner role
        approver="attacker@example.com",
        approved=True,
        reason="I should be allowed!",
    )
    task = _make_task("critical")

    with pytest.raises(PermissionError, match="requires owner approval"):
        handle_approval(task, decision, svc)


def test_empty_owner_identifiers_blocks_all_critical():
    """Empty owner_identifiers → nobody is owner → all critical approvals rejected."""
    svc = _make_services(owner_identifiers=frozenset())
    svc = dataclasses.replace(
        svc,
        store=_make_audit_store("critical"),
        safe_transition=_noop_transition,
    )

    decision = ApprovalDecision(
        task_id="task-001",
        gate="critical",
        approver="anyone@example.com",
        approved=True,
        reason="trying to approve",
    )
    task = _make_task("critical")

    with pytest.raises(PermissionError, match="requires owner approval"):
        handle_approval(task, decision, svc)


def test_no_config_on_services_blocks_critical():
    """If services.config is None (legacy callers), no one can approve critical-tier."""
    svc = PhaseServices(
        store=_make_audit_store("critical"),
        memory=object(),
        channels=None,
        reviewer_invoker=lambda **kw: None,
        transition=_noop_transition,
        safe_transition=_noop_transition,
        notify=lambda *a, **kw: None,
        backend_registry=object(),
        scratch_root=Path("/tmp"),
        repo_path_for_workflow=lambda w: Path("/tmp"),
        workflows_dir=Path("/tmp"),
        config=None,
    )

    decision = ApprovalDecision(
        task_id="task-001",
        gate="critical",
        approver="anyone@example.com",
        approved=True,
        reason="trying",
    )
    task = _make_task("critical")

    with pytest.raises(PermissionError, match="requires owner approval"):
        handle_approval(task, decision, svc)


def test_gate_field_does_not_confer_owner_role():
    """Security: sending gate='critical' does NOT grant owner role to a non-owner."""
    # This is the exact exploit the bug allowed. Verify it is closed.
    svc = _make_services(owner_identifiers=frozenset({"real-owner@example.com"}))
    svc = dataclasses.replace(
        svc,
        store=_make_audit_store("critical"),
        safe_transition=_noop_transition,
    )

    decision = ApprovalDecision(
        task_id="task-001",
        gate="critical",       # attacker sends gate="critical" hoping to escalate
        approver="attacker@example.com",
        approved=True,
        reason="exploit attempt",
    )
    task = _make_task("critical")

    # Must raise — gate field must NOT determine approver_role
    with pytest.raises(PermissionError):
        handle_approval(task, decision, svc)
