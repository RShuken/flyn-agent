from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.types import WorkerSpec, WorkerRole


def test_dispatch_uses_registered_backend(tmp_path: Path):
    fake = MagicMock()
    fake.name = "fake"
    fake.run.return_value = WorkerResult(
        worker_id="w-001", exit_code=0, capture_path=tmp_path / "w-001.jsonl",
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
