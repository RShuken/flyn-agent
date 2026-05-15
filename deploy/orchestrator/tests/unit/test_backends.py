# deploy/orchestrator/tests/unit/test_backends.py
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from flyn_orchestrator.backends import BackendRegistry, get_backend
from flyn_orchestrator.backends.base import WorkerBackend, WorkerResult
from flyn_orchestrator.types import WorkerSpec, WorkerRole


def _spec(tmp_path):
    return WorkerSpec(
        task_id="T-1", worker_id="w-001", role=WorkerRole.BUILDER,
        backend="claude-p", prompt_template="builder",
        worktree_path=str(tmp_path), max_turns=5, budget_usd=1.0,
    )


def test_registry_lookup():
    reg = BackendRegistry()
    fake = MagicMock(spec=WorkerBackend)
    fake.name = "fake-x"
    reg.register("fake-x", fake)
    assert reg.get("fake-x") is fake


def test_claude_p_constructs(tmp_path):
    from flyn_orchestrator.backends.claude_p import ClaudePBackend
    b = ClaudePBackend()
    assert b.name == "claude-p"
    cmd = b._build_command(_spec(tmp_path), prompt="say hi")
    assert "claude" in cmd[0] or cmd[0].endswith("claude")
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--max-turns" in cmd
    assert "5" in cmd


def test_claude_p_allowed_tools_in_command(tmp_path):
    from flyn_orchestrator.backends.claude_p import ClaudePBackend
    spec = WorkerSpec(
        task_id="T-1", worker_id="w-001", role=WorkerRole.BUILDER,
        backend="claude-p", prompt_template="builder",
        worktree_path=str(tmp_path), max_turns=5, budget_usd=1.0,
        allowed_tools=["Read", "Bash"],
    )
    b = ClaudePBackend()
    cmd = b._build_command(spec, prompt="say hi")
    assert "--allowedTools" in cmd
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == "Read,Bash"
