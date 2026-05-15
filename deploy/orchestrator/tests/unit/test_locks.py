from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from flyn_orchestrator.locks import LockManager, LockConflict, LockRecord


@pytest.fixture
def locks(tmp_path):
    return LockManager(locks_dir=tmp_path / "agent_locks")


def test_acquire_first_lock_succeeds(locks):
    locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/api/*.py"])
    active = locks.list_active()
    assert len(active) == 1
    assert active[0].worker_id == "w-1"


def test_acquire_non_overlapping_globs_succeeds(locks):
    locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/api/*"])
    locks.acquire(task_id="T-1", worker_id="w-2", file_globs=["src/web/*"])
    assert len(locks.list_active()) == 2


def test_acquire_overlapping_globs_raises_conflict(locks):
    locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/api/*.py"])
    with pytest.raises(LockConflict):
        locks.acquire(task_id="T-1", worker_id="w-2", file_globs=["src/api/users.py"])


def test_acquire_same_worker_twice_idempotent_or_raises(locks):
    """Acquiring the same worker_id twice should either no-op or raise — but never silently overwrite."""
    locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/api/*.py"])
    with pytest.raises(LockConflict):
        locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/api/*.py"])


def test_release_removes_lock(locks):
    locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/api/*"])
    locks.release("w-1")
    assert len(locks.list_active()) == 0


def test_release_unknown_worker_noop(locks):
    locks.release("never-existed")  # must not raise


def test_expired_lock_does_not_block_new_acquire(locks, monkeypatch):
    """An expired lock from a dead worker shouldn't prevent re-acquire of the same files."""
    # Set time to known
    import flyn_orchestrator.locks as locks_mod
    fake_now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(locks_mod, "_now", lambda: fake_now)
    locks.acquire(task_id="T-1", worker_id="w-old", file_globs=["src/api/*"], ttl_seconds=60)
    # Jump 2 hours forward
    monkeypatch.setattr(locks_mod, "_now", lambda: fake_now + timedelta(hours=2))
    # The expired lock should NOT block a new acquire on the same globs
    locks.acquire(task_id="T-2", worker_id="w-new", file_globs=["src/api/*"])
    assert any(r.worker_id == "w-new" for r in locks.list_active())


def test_prune_expired_removes_dead_locks(locks, monkeypatch):
    import flyn_orchestrator.locks as locks_mod
    fake_now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(locks_mod, "_now", lambda: fake_now)
    locks.acquire(task_id="T-1", worker_id="w-1", file_globs=["src/a/*"], ttl_seconds=60)
    locks.acquire(task_id="T-2", worker_id="w-2", file_globs=["src/b/*"], ttl_seconds=10800)  # 3h — survives 2h jump
    # 2h later: only w-2 is alive
    monkeypatch.setattr(locks_mod, "_now", lambda: fake_now + timedelta(hours=2))
    n = locks.prune_expired()
    assert n == 1
    active = locks.list_active()
    assert len(active) == 1
    assert active[0].worker_id == "w-2"
