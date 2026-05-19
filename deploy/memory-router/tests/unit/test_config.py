from __future__ import annotations

import os
from pathlib import Path

import pytest

from flyn_memory_router.config import Config


def test_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("FLYN_MEMORY_ROUTER_PORT", raising=False)
    cfg = Config.from_env()
    assert cfg.port == 8400
    assert cfg.home == tmp_path
    assert cfg.db_path == tmp_path / "data" / "router.db"
    assert cfg.queue_dir == tmp_path / "queue"
    assert cfg.passthrough_mode is False


def test_port_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_PORT", "9999")
    cfg = Config.from_env()
    assert cfg.port == 9999


def test_passthrough_flag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_PASSTHROUGH", "true")
    cfg = Config.from_env()
    assert cfg.passthrough_mode is True


def test_workspace_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    cfg = Config.from_env()
    assert cfg.workspace == tmp_path / "ws"
    assert cfg.memory_md == tmp_path / "ws" / "MEMORY.md"
    assert cfg.workspace_memory_dir == tmp_path / "ws" / "memory"


def test_read_sources_registry_has_all_ten():
    from flyn_memory_router.config import READ_SOURCES
    expected = {"hot", "warm", "cool", "cold", "lesson",
                "reference", "user", "ol_wiki", "ocw_mem", "lossless"}
    assert set(READ_SOURCES.keys()) == expected


def test_read_sources_defaults_excluded_heavies():
    from flyn_memory_router.config import READ_SOURCES
    assert READ_SOURCES["ocw_mem"].default_included is False
    assert READ_SOURCES["lossless"].default_included is False
    assert READ_SOURCES["hot"].default_included is True


def test_config_has_log_dir_path(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.log_dir.name == "logs"
    assert cfg.log_dir == tmp_path / "logs"


def test_config_has_conv_root_default(monkeypatch, tmp_path):
    from flyn_memory_router.config import Config
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.conv_root == tmp_path / "conv"
    assert cfg.principals_json_path == tmp_path / "conv" / "principals.json"
    assert cfg.conv_owners_db_path == tmp_path / "conv" / "owners.db"


def test_config_conv_root_env_override(monkeypatch, tmp_path):
    from flyn_memory_router.config import Config
    custom = tmp_path / "custom-conv"
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_CONV_ROOT", str(custom))
    cfg = Config.from_env()
    assert cfg.conv_root == custom
    assert cfg.principals_json_path == custom / "principals.json"
