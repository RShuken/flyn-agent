"""Verify that TaskRouter wires a Watchdog by default and routes verdicts
to memory + channel callbacks.

Strategy: build a TaskRouter with a stub triage backend that returns
predetermined verdicts. Trigger one dispatch. Inspect the memory emitter's
recorded events to confirm the callbacks fired.

These tests exercise the wiring (factory builds Watchdog, dispatcher receives
it). The Watchdog's own behaviour is covered in tests/unit/test_watchdog.py.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import (
    InboundTaskRequest,
    ReviewFindings,
    TaskState,
)
from flyn_orchestrator.watchdog import TriageBackend, TriageResult, WatchdogConfig
from flyn_orchestrator.worktree import WorktreeManager


class _StubBackend:
    """Records calls + returns a synthetic WorkerResult that satisfies the
    0-byte capture guard."""

    name = "claude-p"

    def __init__(self, capture_text: str = "x" * 200) -> None:
        self._capture_text = capture_text
        self.calls: list = []

    def run(self, spec, prompt, *, cost_tracker=None):
        self.calls.append((spec, prompt))
        wt = Path(spec.worktree_path)
        wt.mkdir(parents=True, exist_ok=True)
        cap = wt / f"{spec.worker_id}.jsonl"
        cap.write_text(self._capture_text)
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.0, duration_ms=10, changed_files=[], summary="ok",
        )


class _StubTriageBackend:
    """Returns a fixed verdict every time. For tests."""
    name = "stub"

    def __init__(self, verdict: str = "FINE", reason: str = "stub") -> None:
        self._verdict = verdict
        self._reason = reason
        self.calls: list = []

    def classify(self, capture_tail: str, task_intent: str, elapsed_seconds: float) -> TriageResult:
        self.calls.append((capture_tail, task_intent, elapsed_seconds))
        return TriageResult(verdict=self._verdict, reason=self._reason, confidence=0.9)


def _build_router(tmp_path: Path, *, triage_backend=None, watchdog_factory="default", reviewer_invoker=None):
    store = StateStore(tmp_path / "state.db")
    backend = _StubBackend()
    dispatcher = WorkerDispatcher()
    dispatcher.register_backend("claude-p", backend)
    memory = MagicMock(spec=MemoryEmitter)
    memory.emit = MagicMock()
    wt_mgr = WorktreeManager(workspaces_dir=tmp_path / "worktrees")

    # Reviewer stub — happy path passing review
    def _reviewer(**kw):
        return ReviewFindings(passed=True, summary="ok", findings=[])

    router = TaskRouter(
        store=store,
        dispatcher=dispatcher,
        worktree_mgr=wt_mgr,
        memory=memory,
        repo_path_for_workflow=lambda _w: tmp_path / "repo",
        builder_prompt_path=tmp_path / "builder.md",
        reviewer_invoker=reviewer_invoker or _reviewer,
        channel_registry=None,
        workflows=[],
        watchdog_factory=watchdog_factory,
        triage_backend=triage_backend,
    )
    # Ensure the worktree base + repo dir exist; seed a builder prompt
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "builder.md").write_text("Build: {task}\nRequirements: {requirements}\n")
    return router, store, memory, backend


def test_default_watchdog_factory_is_active(tmp_path):
    """With no kwargs, TaskRouter installs the default watchdog factory."""
    router, *_ = _build_router(tmp_path)
    assert router._watchdog_factory is not None
    assert callable(router._watchdog_factory)


def test_watchdog_factory_can_be_disabled(tmp_path):
    """Passing watchdog_factory=None disables wiring."""
    router, *_ = _build_router(tmp_path, watchdog_factory=None)
    assert router._watchdog_factory is None


def test_default_factory_builds_a_watchdog(tmp_path):
    """The default factory returns a Watchdog instance with our triage backend."""
    triage = _StubTriageBackend(verdict="FINE")
    router, *_ = _build_router(tmp_path, triage_backend=triage)
    wd = router._watchdog_factory(
        capture_path=tmp_path / "fake.jsonl",
        task_id="T-0001",
        task_intent="test",
    )
    assert wd is not None
    assert wd._backend is triage


def test_stuck_verdict_emits_memory_event(tmp_path):
    """When the watchdog classifies STUCK consecutively, the default on_stuck
    callback emits a `worker_stuck` memory event."""
    triage = _StubTriageBackend(verdict="STUCK", reason="no progress for 60s")
    router, _store, memory, _backend = _build_router(tmp_path, triage_backend=triage)

    # Construct the watchdog directly and drive 2 polls (threshold=2 for STUCK).
    wd = router._watchdog_factory(
        capture_path=tmp_path / "cap.jsonl",
        task_id="T-0042",
        task_intent="test intent",
    )
    # Seed a capture file so _poll_once actually reads + calls classify
    (tmp_path / "cap.jsonl").write_text("some output")
    wd._start_time = 0
    wd._poll_once()
    wd._poll_once()  # second poll triggers on_stuck

    # Verify memory.emit was called with event_type="worker_stuck"
    stuck_calls = [
        c for c in memory.emit.call_args_list
        if c.kwargs.get("event_type") == "worker_stuck"
    ]
    assert stuck_calls, f"Expected worker_stuck emit; got: {[c.kwargs.get('event_type') for c in memory.emit.call_args_list]}"
    assert "T-0042" in stuck_calls[0].kwargs["subject"]
    assert "no progress for 60s" in stuck_calls[0].kwargs["body"]


def test_escalate_verdict_emits_memory_event_immediately(tmp_path):
    """ESCALATE fires on_escalate WITHOUT waiting for consecutive_stuck threshold."""
    triage = _StubTriageBackend(verdict="ESCALATE", reason="auth lockout loop")
    router, _store, memory, _backend = _build_router(tmp_path, triage_backend=triage)

    wd = router._watchdog_factory(
        capture_path=tmp_path / "cap.jsonl",
        task_id="T-0099",
        task_intent="escalate test",
    )
    (tmp_path / "cap.jsonl").write_text("blah blah")
    wd._start_time = 0
    wd._poll_once()  # first poll already triggers ESCALATE

    escalate_calls = [
        c for c in memory.emit.call_args_list
        if c.kwargs.get("event_type") == "worker_escalate"
    ]
    assert escalate_calls
    assert "auth lockout loop" in escalate_calls[0].kwargs["body"]


def test_fine_verdict_does_not_emit_event(tmp_path):
    """A FINE verdict is a no-op — no memory events fired by default callbacks."""
    triage = _StubTriageBackend(verdict="FINE")
    router, _store, memory, _backend = _build_router(tmp_path, triage_backend=triage)

    wd = router._watchdog_factory(
        capture_path=tmp_path / "cap.jsonl",
        task_id="T-0001",
        task_intent="fine test",
    )
    (tmp_path / "cap.jsonl").write_text("good output")
    wd._start_time = 0
    wd._poll_once()

    # No `worker_*` memory event for FINE verdict
    bad_events = [
        c for c in memory.emit.call_args_list
        if (c.kwargs.get("event_type") or "").startswith("worker_")
        and c.kwargs.get("event_type") not in ("worker_dispatched", "worker_exit")
    ]
    assert not bad_events, f"FINE should be no-op; got: {bad_events}"
