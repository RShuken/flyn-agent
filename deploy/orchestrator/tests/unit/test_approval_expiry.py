"""Unit tests for Phase 5b approval expiry helpers.

The integration tests at test_router_ops.py exercise the end-to-end behavior
through router.handle_approval. These tests focus on the pure helper functions
(_is_approval_expired, _approval_window_seconds) where edge-cases are easier
to assert.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from flyn_orchestrator.ops_phase import (
    _approval_window_seconds,
    _is_approval_expired,
)


# ---------------------------------------------------------------------------
# Window lookup
# ---------------------------------------------------------------------------

def test_window_medium_is_2h():
    assert _approval_window_seconds("medium") == 7200


def test_window_high_is_1h():
    assert _approval_window_seconds("high") == 3600


def test_window_critical_is_30min():
    assert _approval_window_seconds("critical") == 1800


def test_window_low_returns_none():
    """Low tier auto-executes (no approval flow); no window."""
    assert _approval_window_seconds("low") is None


def test_window_unknown_tier_returns_none():
    assert _approval_window_seconds("xyzzy") is None


# ---------------------------------------------------------------------------
# Expiry check
# ---------------------------------------------------------------------------

def test_expired_when_high_tier_elapsed_beyond_window():
    issued = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, 13, 30, 0, tzinfo=timezone.utc)  # +90 min
    assert _is_approval_expired(issued, "high", now=now) is True


def test_not_expired_when_high_tier_within_window():
    issued = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, 12, 30, 0, tzinfo=timezone.utc)  # +30 min (window=1h)
    assert _is_approval_expired(issued, "high", now=now) is False


def test_expired_when_critical_beyond_30min():
    issued = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, 12, 31, 0, tzinfo=timezone.utc)  # +31 min (window=30min)
    assert _is_approval_expired(issued, "critical", now=now) is True


def test_not_expired_when_critical_at_29_minutes():
    issued = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, 12, 29, 0, tzinfo=timezone.utc)
    assert _is_approval_expired(issued, "critical", now=now) is False


def test_not_expired_when_issued_at_missing():
    """Legacy tasks without approval_issued_at have no expiry check (returns False)."""
    assert _is_approval_expired(None, "high") is False
    assert _is_approval_expired("", "high") is False


def test_not_expired_when_tier_has_no_window():
    """Low-tier tasks (or unknown tiers) have no window → never expire."""
    issued = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)  # weeks later
    assert _is_approval_expired(issued, "low", now=now) is False
    assert _is_approval_expired(issued, "xyzzy", now=now) is False


def test_not_expired_when_issued_at_unparseable():
    """Garbage ISO string → no expiry check (graceful, no exception)."""
    assert _is_approval_expired("not-a-date", "high") is False
    assert _is_approval_expired("2026-13-99T99:99:99Z", "high") is False


def test_handles_naive_datetime_isoformat():
    """If issued_at was serialized without tz info, helper treats it as UTC."""
    issued = "2026-05-18T12:00:00"   # no tz
    now = datetime(2026, 5, 18, 13, 30, 0, tzinfo=timezone.utc)
    assert _is_approval_expired(issued, "high", now=now) is True


def test_uses_real_now_when_no_now_passed():
    """Helper defaults to datetime.now(timezone.utc); a freshly-issued
    timestamp should not be expired."""
    issued = datetime.now(timezone.utc).isoformat()
    assert _is_approval_expired(issued, "high") is False


def test_exactly_at_window_boundary_is_not_expired():
    """Window is "elapsed > window", so elapsed == window is NOT expired."""
    issued = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, 13, 0, 0, tzinfo=timezone.utc)  # exactly +1h
    assert _is_approval_expired(issued, "high", now=now) is False


def test_one_second_past_window_is_expired():
    issued = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 5, 18, 13, 0, 1, tzinfo=timezone.utc)  # +1h +1s
    assert _is_approval_expired(issued, "high", now=now) is True
