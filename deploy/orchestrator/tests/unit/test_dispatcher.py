from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.dispatcher import WorkerDispatcher, WorkerProducedNothing
from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.types import WorkerSpec, WorkerRole


def test_dispatch_uses_registered_backend(tmp_path: Path):
    fake = MagicMock()
    fake.name = "fake"
    cap = tmp_path / "w-001.jsonl"
    cap.write_text('{"type":"message","content":"hello world test"}\n' * 5)  # > 100 bytes
    fake.run.return_value = WorkerResult(
        worker_id="w-001", exit_code=0, capture_path=cap,
        cost_usd=0.05, duration_ms=100, changed_files=["a.py"], summary="ok",
    )
    d = WorkerDispatcher()
    d.register_backend("fake", fake)
    spec = WorkerSpec(
        task_id="T-1", worker_id="w-001", role=WorkerRole.BUILDER,
        backend="fake", prompt_template="builder",
        worktree_path=str(tmp_path), max_turns=5, budget_usd=1.0,
    )
    res = d.dispatch(spec, prompt="hi")
    assert res.exit_code == 0
    assert fake.run.called


def test_dispatch_raises_on_zero_byte_capture(tmp_path: Path):
    fake = MagicMock()
    fake.name = "fake"
    cap = tmp_path / "empty.jsonl"
    cap.touch()  # 0 bytes
    fake.run.return_value = WorkerResult(
        worker_id="w", exit_code=0, capture_path=cap,
        cost_usd=0.0, duration_ms=10, changed_files=[], summary="",
    )
    d = WorkerDispatcher()
    d.register_backend("fake", fake)
    spec = WorkerSpec(
        task_id="T-1", worker_id="w", role=WorkerRole.BUILDER,
        backend="fake", prompt_template="builder",
        worktree_path=str(tmp_path), max_turns=5, budget_usd=1.0,
    )
    with pytest.raises(WorkerProducedNothing):
        d.dispatch(spec, prompt="x")


def test_dispatch_accepts_normal_capture(tmp_path: Path):
    fake = MagicMock()
    fake.name = "fake"
    cap = tmp_path / "good.jsonl"
    cap.write_text('{"type":"message","content":"hi"}\n' * 5)  # > 100 bytes
    fake.run.return_value = WorkerResult(
        worker_id="w", exit_code=0, capture_path=cap,
        cost_usd=0.0, duration_ms=10, changed_files=[], summary="ok",
    )
    d = WorkerDispatcher()
    d.register_backend("fake", fake)
    spec = WorkerSpec(
        task_id="T-1", worker_id="w", role=WorkerRole.BUILDER,
        backend="fake", prompt_template="builder",
        worktree_path=str(tmp_path), max_turns=5, budget_usd=1.0,
    )
    res = d.dispatch(spec, prompt="x")
    assert res.exit_code == 0
