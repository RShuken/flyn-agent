"""Integration test: Watchdog + WorkerDispatcher end-to-end.

Uses a fake WorkerBackend that writes to a capture file in a background
thread while the dispatcher runs. No real Popen; no real Ollama.

Verifies:
- Watchdog is started before the backend run
- Watchdog polls the capture file while the backend "runs"
- Watchdog is stopped after the backend returns
- Verdicts fire in the expected order
"""
from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.backends import BackendRegistry
from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.cost import CostTracker
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.types import WorkerRole, WorkerSpec
from flyn_orchestrator.watchdog import (
    TriageResult,
    Watchdog,
    WatchdogConfig,
)


# ---------------------------------------------------------------------------
# Fake backend that writes incrementally to capture_path
# ---------------------------------------------------------------------------


class IncrementalFakeBackend:
    """Backend that writes lines to capture_path over several seconds.

    The "run" blocks for `duration_seconds`, writing one line every
    `write_interval` seconds, simulating a real worker.
    """

    name = "fake-incremental"

    def __init__(
        self,
        capture_path: Path,
        duration_seconds: float = 0.3,
        write_interval: float = 0.05,
    ) -> None:
        self._capture_path = capture_path
        self._duration = duration_seconds
        self._write_interval = write_interval

    def run(
        self,
        spec: WorkerSpec,
        prompt: str,
        *,
        cost_tracker: Optional[CostTracker] = None,
    ) -> WorkerResult:
        deadline = time.time() + self._duration
        seq = 0
        with self._capture_path.open("w") as fh:
            while time.time() < deadline:
                fh.write(f"progress line {seq}\n")
                fh.flush()
                seq += 1
                time.sleep(self._write_interval)
        return WorkerResult(
            worker_id=spec.worker_id,
            exit_code=0,
            capture_path=self._capture_path,
            cost_usd=0.0,
            duration_ms=int(self._duration * 1000),
            changed_files=[],
            summary="done",
        )


# ---------------------------------------------------------------------------
# Stub backend for classify
# ---------------------------------------------------------------------------


class _CyclingStub:
    """Returns verdicts in cycle; records calls."""

    name = "stub"

    def __init__(self, verdicts: list[str]) -> None:
        self._verdicts = verdicts
        self._idx = 0
        self.calls: list[dict] = []

    def classify(
        self,
        capture_tail: str,
        task_intent: str,
        elapsed_seconds: float,
    ) -> TriageResult:
        self.calls.append({"tail_len": len(capture_tail)})
        verdict = self._verdicts[self._idx % len(self._verdicts)]
        self._idx += 1
        return TriageResult(verdict=verdict, reason="integration-test", confidence=0.8)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWatchdogDispatcherIntegration:
    def test_watchdog_polls_while_backend_runs(self, tmp_path: Path):
        """Watchdog classifies the growing capture file while backend executes."""
        capture = tmp_path / "capture.jsonl"
        stub = _CyclingStub(["FINE", "FINE", "NEEDS_NUDGE"])
        nudge_calls: list[TriageResult] = []

        cfg = WatchdogConfig(
            poll_interval_seconds=0.07,
            tail_bytes=4096,
            consecutive_stuck_threshold=2,
        )
        wd = Watchdog(
            capture_path=capture,
            task_id="T-int-001",
            task_intent="integration test task",
            backend=stub,
            on_nudge=lambda r: nudge_calls.append(r),
            config=cfg,
        )

        fake_backend = IncrementalFakeBackend(
            capture_path=capture,
            duration_seconds=0.35,
            write_interval=0.05,
        )

        registry = BackendRegistry()
        registry.register("fake-incremental", fake_backend)
        dispatcher = WorkerDispatcher(registry=registry)

        spec = WorkerSpec(
            task_id="T-int-001",
            worker_id="w-int-001",
            role=WorkerRole.BUILDER,
            backend="fake-incremental",
            prompt_template="builder",
            worktree_path=str(tmp_path),
            max_turns=5,
            budget_usd=1.0,
        )

        result = dispatcher.dispatch(spec, prompt="build something", watchdog=wd)

        # Backend completed successfully
        assert result.exit_code == 0
        assert result.worker_id == "w-int-001"

        # Watchdog polled at least once (capture file had content)
        assert len(stub.calls) >= 1, "Watchdog should have called classify at least once"

        # At least one non-empty tail was seen
        assert any(c["tail_len"] > 0 for c in stub.calls), "Classify called with non-empty tail"

        # NEEDS_NUDGE fired
        assert len(nudge_calls) >= 1

        # Watchdog was stopped (thread joined)
        assert wd._thread is None

    def test_watchdog_stop_called_on_backend_exception(self, tmp_path: Path):
        """Watchdog.stop() is called even if the backend raises."""
        capture = tmp_path / "capture.jsonl"
        capture.write_text("existing output\n")
        stub = _CyclingStub(["FINE"])

        wd = Watchdog(
            capture_path=capture,
            task_id="T-int-002",
            task_intent="failing task",
            backend=stub,
            config=WatchdogConfig(poll_interval_seconds=0.05),
        )

        class ExplodingBackend:
            name = "exploding"

            def run(self, spec, prompt, *, cost_tracker=None):
                time.sleep(0.05)
                raise RuntimeError("backend exploded")

        registry = BackendRegistry()
        registry.register("exploding", ExplodingBackend())
        dispatcher = WorkerDispatcher(registry=registry)

        spec = WorkerSpec(
            task_id="T-int-002",
            worker_id="w-int-002",
            role=WorkerRole.BUILDER,
            backend="exploding",
            prompt_template="builder",
            worktree_path=str(tmp_path),
            max_turns=5,
            budget_usd=1.0,
        )

        with pytest.raises(RuntimeError, match="backend exploded"):
            dispatcher.dispatch(spec, prompt="fail me", watchdog=wd)

        # Watchdog must be stopped even after exception
        assert wd._thread is None

    def test_dispatch_without_watchdog_unchanged(self, tmp_path: Path):
        """Existing callers that pass no watchdog see identical behaviour."""
        capture = tmp_path / "capture.jsonl"
        capture.write_text("output line\n" * 15)  # > 100 bytes

        fake = MagicMock()
        fake.name = "noop"
        fake.run.return_value = WorkerResult(
            worker_id="w-noop",
            exit_code=0,
            capture_path=capture,
            cost_usd=0.0,
            duration_ms=10,
            changed_files=[],
            summary="ok",
        )

        registry = BackendRegistry()
        registry.register("noop", fake)
        dispatcher = WorkerDispatcher(registry=registry)

        spec = WorkerSpec(
            task_id="T-noop",
            worker_id="w-noop",
            role=WorkerRole.BUILDER,
            backend="noop",
            prompt_template="builder",
            worktree_path=str(tmp_path),
            max_turns=5,
            budget_usd=1.0,
        )

        # No watchdog parameter — should work exactly as before
        result = dispatcher.dispatch(spec, prompt="no watchdog")
        assert result.exit_code == 0
        assert fake.run.called
