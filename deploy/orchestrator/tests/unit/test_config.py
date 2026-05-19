from pathlib import Path
import pytest
from flyn_orchestrator.config import Config


def test_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_ORCHESTRATOR_HOME", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.port == 8300
    assert cfg.home == tmp_path
    assert cfg.db_path == tmp_path / "data" / "state.db"
    assert cfg.workspaces_dir == tmp_path / "workspaces"
    assert cfg.captures_dir == tmp_path / "captures"
    assert cfg.router_url == "http://localhost:8400"


def test_port_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_ORCHESTRATOR_PORT", "9300")
    assert Config.from_env().port == 9300


def test_default_backend(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.delenv("FLYN_DEFAULT_BACKEND", raising=False)
    assert Config.from_env().default_backend == "noop"
