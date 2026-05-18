"""Tests for the Phase 5b approval sweep — proactively transitions stale
AWAITING_OWNER_APPROVAL tasks to REJECTED without waiting for an approval
attempt to arrive.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.ops_phase import sweep_expired_approvals
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import TaskRecord, TaskState


def _store(tmp_path):
    return StateStore(db_path=tmp_path / "state.db")


def _build_awaiting_task(
    store: StateStore,
    task_id: str,
    *,
    tier: str = "high",
    issued_at_iso: str | None = None,
) -> TaskRecord:
    """Insert a task and transition it to AWAITING_OWNER_APPROVAL with the
    specified tier + issued_at on payload."""
    t = TaskRecord(
        task_id=task_id, workflow="ops", state=TaskState.INBOUND,
        sender_role="owner", sender_identifier="test",
        intent=f"test task {task_id}",
        created_at=datetime.now(timezone.utc),
        budget_usd=1.0,
        raw_payload={},
    )
    store.insert_task(t)
    # Walk through states to reach AWAITING_OWNER_APPROVAL (mimics ops_phase.run).
    for from_s, to_s in [
        (TaskState.INBOUND, TaskState.TRIAGING),
        (TaskState.TRIAGING, TaskState.ROUTED),
        (TaskState.ROUTED, TaskState.DECOMPOSED),
        (TaskState.DECOMPOSED, TaskState.DISPATCHED),
        (TaskState.DISPATCHED, TaskState.RUNNING),
        (TaskState.RUNNING, TaskState.AWAITING_OWNER_APPROVAL),
    ]:
        store.transition(task_id, from_s, to_s, actor="test-fixture", reason="setup")
    # Stamp the payload with tier + issued_at + ops_spec for sweep introspection.
    payload_update: dict = {"risk_tier": tier, "ops_spec": {"target": "/test/target"}}
    if issued_at_iso is not None:
        payload_update["approval_issued_at"] = issued_at_iso
    store.update_task_payload(task_id, payload_update)
    return store.get_task(task_id)


def test_sweep_transitions_expired_high_tier(tmp_path):
    """A high-tier task with issued_at 2h ago (>1h window) is transitioned to REJECTED."""
    store = _store(tmp_path)
    issued = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _build_awaiting_task(store, "T-expire-1", tier="high", issued_at_iso=issued)

    result = sweep_expired_approvals(store)
    assert len(result) == 1
    assert result[0]["task_id"] == "T-expire-1"
    assert result[0]["tier"] == "high"
    assert result[0]["window_seconds"] == 3600
    assert result[0]["elapsed_seconds"] >= 7200

    # Verify state transition
    task = store.get_task("T-expire-1")
    assert task.state == TaskState.REJECTED

    # Verify audit row
    audit = store.list_audit("T-expire-1")
    actions = [r["action"] for r in audit]
    assert "approval_expired" in actions
    expired_row = next(r for r in audit if r["action"] == "approval_expired")
    assert expired_row["actor"] == "sweep"


def test_sweep_skips_fresh_approvals(tmp_path):
    """Tasks within their tier's window are NOT transitioned."""
    store = _store(tmp_path)
    issued = datetime.now(timezone.utc).isoformat()
    _build_awaiting_task(store, "T-fresh-1", tier="high", issued_at_iso=issued)

    result = sweep_expired_approvals(store)
    assert result == []

    task = store.get_task("T-fresh-1")
    assert task.state == TaskState.AWAITING_OWNER_APPROVAL


def test_sweep_skips_legacy_tasks_without_issued_at(tmp_path):
    """A task without `approval_issued_at` on payload (legacy task) is NOT swept.

    The sweep only acts on explicit timestamps; legacy tasks must be expired
    via an approval attempt (which will set issued_at_iso=None → graceful
    no-expiry per `_is_approval_expired`)."""
    store = _store(tmp_path)
    _build_awaiting_task(store, "T-legacy-1", tier="high", issued_at_iso=None)

    result = sweep_expired_approvals(store)
    assert result == []
    task = store.get_task("T-legacy-1")
    assert task.state == TaskState.AWAITING_OWNER_APPROVAL


def test_sweep_handles_critical_tier_window(tmp_path):
    """Critical tier has a 30-minute window."""
    store = _store(tmp_path)
    issued = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    _build_awaiting_task(store, "T-crit-1", tier="critical", issued_at_iso=issued)

    result = sweep_expired_approvals(store)
    assert len(result) == 1
    assert result[0]["task_id"] == "T-crit-1"
    assert result[0]["window_seconds"] == 1800


def test_sweep_skips_low_tier_tasks_with_issued_at(tmp_path):
    """Low-tier tasks never have an approval window. Even with issued_at set,
    they should not be swept. (In practice low-tier never reaches
    AWAITING_OWNER_APPROVAL, but the helper should be robust.)"""
    store = _store(tmp_path)
    issued = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _build_awaiting_task(store, "T-low-1", tier="low", issued_at_iso=issued)

    result = sweep_expired_approvals(store)
    assert result == []
    task = store.get_task("T-low-1")
    assert task.state == TaskState.AWAITING_OWNER_APPROVAL


def test_sweep_processes_multiple_tasks(tmp_path):
    """Sweep handles multiple expired tasks in one call."""
    store = _store(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    _build_awaiting_task(store, "T-1", tier="high", issued_at_iso=old)
    _build_awaiting_task(store, "T-2", tier="high", issued_at_iso=old)
    _build_awaiting_task(store, "T-3", tier="high", issued_at_iso=fresh)

    result = sweep_expired_approvals(store)
    assert len(result) == 2
    transitioned_ids = {r["task_id"] for r in result}
    assert transitioned_ids == {"T-1", "T-2"}
    assert store.get_task("T-1").state == TaskState.REJECTED
    assert store.get_task("T-2").state == TaskState.REJECTED
    assert store.get_task("T-3").state == TaskState.AWAITING_OWNER_APPROVAL


def test_sweep_emits_memory_event_when_emitter_provided(tmp_path):
    """If memory_emitter is passed, each transition fires an event."""
    store = _store(tmp_path)
    issued = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _build_awaiting_task(store, "T-mem-1", tier="high", issued_at_iso=issued)

    emitter = MagicMock()
    sweep_expired_approvals(store, memory_emitter=emitter)
    emitter.emit.assert_called_once()
    kwargs = emitter.emit.call_args.kwargs
    assert kwargs["event_type"] == "ops_approval_expired"
    assert kwargs["subject"] == "T-mem-1"
    assert "Sweep:" in kwargs["body"]


def test_sweep_swallows_broken_memory_emitter(tmp_path):
    """A broken memory emitter must not break the sweep — transition still happens."""
    store = _store(tmp_path)
    issued = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _build_awaiting_task(store, "T-broken-1", tier="high", issued_at_iso=issued)

    bad_emitter = MagicMock()
    bad_emitter.emit = MagicMock(side_effect=RuntimeError("emitter down"))

    # Must not raise; sweep continues
    result = sweep_expired_approvals(store, memory_emitter=bad_emitter)
    assert len(result) == 1
    assert store.get_task("T-broken-1").state == TaskState.REJECTED


def test_sweep_is_idempotent(tmp_path):
    """Second call after the first finds no candidates (already transitioned)."""
    store = _store(tmp_path)
    issued = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _build_awaiting_task(store, "T-idemp-1", tier="high", issued_at_iso=issued)

    first = sweep_expired_approvals(store)
    second = sweep_expired_approvals(store)
    assert len(first) == 1
    assert len(second) == 0


def test_sweep_respects_explicit_now(tmp_path):
    """Passing now= lets callers control the reference time (useful for tests)."""
    store = _store(tmp_path)
    issued = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    _build_awaiting_task(store, "T-now-1", tier="high", issued_at_iso=issued)

    # Reference time exactly 30 min after issuance → not expired (window=1h)
    now = datetime(2026, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    assert sweep_expired_approvals(store, now=now) == []

    # Reference time 2h after → expired
    now = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc)
    result = sweep_expired_approvals(store, now=now)
    assert len(result) == 1
