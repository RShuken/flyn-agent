from __future__ import annotations

import pytest


def test_health_tracker_records_success():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=100)
    ht.record(source="hot", elapsed_ms=42, error=False)
    snap = ht.snapshot("hot")
    assert snap["last_elapsed_ms"] == 42
    assert snap["last_error_ts"] is None
    assert snap["error_rate_100q"] == 0.0


def test_health_tracker_records_error():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=100)
    ht.record(source="warm", elapsed_ms=0, error=True)
    snap = ht.snapshot("warm")
    assert snap["last_error_ts"] is not None
    assert snap["error_rate_100q"] == 1.0


def test_health_tracker_rolls_window():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=3)
    ht.record("hot", elapsed_ms=10, error=True)
    ht.record("hot", elapsed_ms=10, error=False)
    ht.record("hot", elapsed_ms=10, error=False)
    assert ht.snapshot("hot")["error_rate_100q"] == pytest.approx(1/3)
    ht.record("hot", elapsed_ms=10, error=False)
    assert ht.snapshot("hot")["error_rate_100q"] == 0.0


def test_unknown_source_snapshot_is_empty():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=100)
    snap = ht.snapshot("never-seen")
    assert snap["last_elapsed_ms"] is None
    assert snap["error_rate_100q"] is None


def test_all_snapshots_does_not_deadlock():
    """Regression for the all_snapshots() deadlock when Lock wasn't reentrant."""
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=10)
    ht.record("hot", elapsed_ms=5, error=False)
    ht.record("warm", elapsed_ms=10, error=True)
    snaps = ht.all_snapshots()
    assert set(snaps.keys()) == {"hot", "warm"}
    assert snaps["hot"]["error_rate_100q"] == 0.0
    assert snaps["warm"]["error_rate_100q"] == 1.0
