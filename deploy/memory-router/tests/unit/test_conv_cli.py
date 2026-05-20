"""flyn-mem conv subcommand cluster: health, search, replay."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_CONV_ROOT", str(tmp_path / "conv"))
    monkeypatch.setenv("USER", "ryan")
    (tmp_path / "conv").mkdir(parents=True)
    (tmp_path / "conv" / "principals.json").write_text(json.dumps({
        "owners": [{"id": "ryan", "display_name": "Ryan",
                    "principals": {"telegram": "7191564227"}}]
    }))
    # Stub Keychain so replay doesn't try to talk to security CLI
    from flyn_memory_router.conv import encrypted_raw
    encrypted_raw._get_key.cache_clear()
    monkeypatch.setattr(encrypted_raw, "_get_key", lambda owner_id: b"k" * 16)
    return tmp_path


def _seed_row(env_path: Path):
    """Insert a row directly so health/search/replay have something to find."""
    from flyn_memory_router.conv.schema import ConvDb, ConvMessage
    from flyn_memory_router.conv import encrypted_raw
    db = ConvDb("ryan", env_path / "conv" / "ryan.db")
    sealed = encrypted_raw.seal(
        json.dumps({"channel": "telegram", "text": "Linear backlog"}).encode(),
        "ryan",
    )
    db.write(ConvMessage(
        channel="telegram", sender_id="7191564227",
        thread_id="t1", reply_to_id=None,
        ts="2026-05-19T18:00:00+00:00",
        body="Linear backlog at 73 of 124",
        attachments=[],
        encrypted_raw=sealed,
    ))


def test_health_prints_per_owner_stats(env, capsys):
    """flyn-mem conv health prints row count for each owner."""
    _seed_row(env)
    from flyn_memory_router.cli import main
    rc = main(["conv", "health"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ryan" in out
    assert "1" in out  # row count


def test_search_finds_seeded_row(env, capsys):
    _seed_row(env)
    from flyn_memory_router.cli import main
    rc = main(["conv", "search", "linear backlog"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "73 of 124" in out


def test_replay_decrypts_and_prints(env, capsys):
    """replay <id> calls unseal and prints the JSON."""
    _seed_row(env)
    from flyn_memory_router.cli import main
    rc = main(["conv", "replay", "1", "--owner", "ryan"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Linear backlog" in out
    # Confirm audit row was written
    from flyn_memory_router.conv.owner import OwnerRegistry
    from flyn_memory_router.config import Config
    cfg = Config.from_env()
    registry = OwnerRegistry(cfg.conv_owners_db_path, cfg.principals_json_path)
    audit = registry.recent_audit()
    assert any(r["op"] == "replay" for r in audit)
