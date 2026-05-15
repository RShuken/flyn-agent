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
    assert "--verbose" in cmd
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


def test_claude_p_includes_anthropic_api_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fallback-key")
    from flyn_orchestrator.backends.claude_p import ClaudePBackend
    b = ClaudePBackend()
    env = b._build_env()
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-test-fallback-key"


def test_claude_p_loads_anthropic_key_from_auth_profiles(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "flyn_orchestrator.backends.claude_p._load_anthropic_api_key_from_profiles",
        lambda: "sk-ant-from-profile",
    )
    from flyn_orchestrator.backends.claude_p import ClaudePBackend
    b = ClaudePBackend()
    env = b._build_env()
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-from-profile"


def test_claude_p_does_not_set_key_if_none_available(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "flyn_orchestrator.backends.claude_p._load_anthropic_api_key_from_profiles",
        lambda: None,
    )
    from flyn_orchestrator.backends.claude_p import ClaudePBackend
    b = ClaudePBackend()
    env = b._build_env()
    assert "ANTHROPIC_API_KEY" not in env


def test_codex_exec_constructs(tmp_path):
    from flyn_orchestrator.backends.codex_exec import CodexExecBackend
    b = CodexExecBackend()
    assert b.name == "codex-exec"
    spec = WorkerSpec(
        task_id="T-1", worker_id="w-001", role=WorkerRole.BUILDER,
        backend="codex-exec", prompt_template="builder",
        worktree_path=str(tmp_path), max_turns=5, budget_usd=1.0,
    )
    cmd = b._build_command(spec, "hello")
    assert cmd[0].endswith("codex") or cmd[0] == "codex"
    assert "exec" in cmd
    assert "--json" in cmd
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd


def test_codex_exec_registered_by_default():
    from flyn_orchestrator.backends import get_backend
    b = get_backend("codex-exec")
    assert b.name == "codex-exec"


def test_codex_exec_env_includes_openai_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-codex-key")
    from flyn_orchestrator.backends.codex_exec import CodexExecBackend
    b = CodexExecBackend()
    env = b._build_env()
    assert env.get("OPENAI_API_KEY") == "sk-proj-test-codex-key"


def test_codex_exec_env_loads_from_auth_profiles(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "flyn_orchestrator.backends.codex_exec._load_openai_api_key_from_profiles",
        lambda: "sk-proj-from-profile",
    )
    from flyn_orchestrator.backends.codex_exec import CodexExecBackend
    b = CodexExecBackend()
    env = b._build_env()
    assert env.get("OPENAI_API_KEY") == "sk-proj-from-profile"
