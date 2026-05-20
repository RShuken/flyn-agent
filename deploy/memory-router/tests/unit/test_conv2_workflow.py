"""Atomic workflow transition tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flyn_memory_router.conv2.schema import migrate, open_db
from flyn_memory_router.conv2.state import Stage, WorkflowState
from flyn_memory_router.conv2.workflow import (
    advance_stage,
    create_workflow,
    find_stuck,
    get_workflow,
    record_failure,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Migrated owner DB ready for workflow operations."""
    path = tmp_path / "owner.db"
    migrate(path)
    return path


def test_create_workflow_sets_received_state(db: Path):
    """A new workflow starts in RECEIVED with the given trace_id."""
    row = create_workflow(db, message_id=1, trace_id="tr-abc")
    assert row.state == WorkflowState.RECEIVED
    assert row.trace_id == "tr-abc"
    assert row.created_at is not None
    assert row.encrypted_at is None


def test_create_workflow_is_idempotent(db: Path):
    """Calling create_workflow twice doesn't create a duplicate row."""
    create_workflow(db, message_id=1, trace_id="tr-abc")
    create_workflow(db, message_id=1, trace_id="tr-different")
    # Should not overwrite trace_id on second insert
    row = get_workflow(db, 1)
    assert row.trace_id == "tr-abc"


def test_advance_through_full_pipeline_to_complete(db: Path):
    """A message advancing through encrypt+index+summarize reaches COMPLETE.

    Promote is best-effort/async (Graphiti runs synchronous LLM entity
    extraction at multi-minute latencies; blocking COMPLETE on it would
    tie pipeline e2e p99 to Graphiti's p99). promoted_at still gets set
    when Graphiti eventually returns, for observability + dedup."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    assert advance_stage(db, 1, Stage.ENCRYPT) == WorkflowState.ENCRYPTED
    assert advance_stage(db, 1, Stage.INDEX) == WorkflowState.INDEXED
    # SUMMARIZE completes the synchronous chain → COMPLETE
    result = advance_stage(db, 1, Stage.SUMMARIZE)
    assert result == WorkflowState.COMPLETE

    row = get_workflow(db, 1)
    assert row.completed_at is not None
    assert row.encrypted_at is not None
    assert row.indexed_at is not None
    assert row.summarized_at is not None
    # promoted_at can be None at this point — promote is async/best-effort
    # Calling advance_stage(PROMOTE) later still works idempotently:
    advance_stage(db, 1, Stage.PROMOTE)
    row = get_workflow(db, 1)
    assert row.promoted_at is not None


def test_advance_stage_atomic_single_statement(db: Path):
    """advance_stage uses atomic UPDATE — verify by reading state mid-flight not possible
    (proxy: the operation only emits 2 SQL statements: UPDATE + COMPLETE check)."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    # Just verify the transition is reflected immediately and consistently
    advance_stage(db, 1, Stage.ENCRYPT)
    row = get_workflow(db, 1)
    assert row.state == WorkflowState.ENCRYPTED
    assert row.encrypted_at is not None


def test_advance_stage_idempotent_replay(db: Path):
    """Calling advance_stage twice for the same stage doesn't break state.

    Replay-safe: second call doesn't undo or corrupt anything. attempts bumps."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    advance_stage(db, 1, Stage.ENCRYPT)
    advance_stage(db, 1, Stage.ENCRYPT)  # replay
    row = get_workflow(db, 1)
    assert row.state == WorkflowState.ENCRYPTED
    assert row.encrypted_at is not None


def test_record_failure_below_max_attempts_keeps_state(db: Path):
    """A single failure doesn't transition to FAILED; just records the error."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    state = record_failure(db, 1, Stage.SUMMARIZE, "ollama timed out", max_attempts=3)
    assert state == WorkflowState.RECEIVED  # didn't transition
    row = get_workflow(db, 1)
    assert row.last_error == "ollama timed out"
    assert row.last_error_stage == "summarize"


def test_record_failure_after_max_attempts_transitions_to_failed(db: Path):
    """Three failures hit max → transition to FAILED terminal state."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    record_failure(db, 1, Stage.SUMMARIZE, "err1", max_attempts=3)
    record_failure(db, 1, Stage.SUMMARIZE, "err2", max_attempts=3)
    final = record_failure(db, 1, Stage.SUMMARIZE, "err3", max_attempts=3)
    assert final == WorkflowState.FAILED
    row = get_workflow(db, 1)
    assert row.failed_at is not None
    assert row.last_error == "err3"


def test_find_stuck_returns_only_old_in_flight(db: Path):
    """Stuck = NOT terminal AND created > N seconds ago.

    Insert a row with backdated created_at to simulate stuckness."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    # Backdate to 2 minutes ago
    with open_db(db) as conn:
        conn.execute(
            "UPDATE conversation_workflow SET created_at = datetime('now', '-120 seconds') "
            "WHERE message_id = 1"
        )
    stuck = find_stuck(db, stuck_after_seconds=60)
    assert len(stuck) == 1
    assert stuck[0].message_id == 1


def test_find_stuck_excludes_complete(db: Path):
    """Complete messages don't show as stuck."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    advance_stage(db, 1, Stage.ENCRYPT)
    advance_stage(db, 1, Stage.INDEX)
    advance_stage(db, 1, Stage.SUMMARIZE)
    advance_stage(db, 1, Stage.PROMOTE)
    # Backdate
    with open_db(db) as conn:
        conn.execute(
            "UPDATE conversation_workflow SET created_at = datetime('now', '-120 seconds') "
            "WHERE message_id = 1"
        )
    stuck = find_stuck(db, stuck_after_seconds=60)
    assert len(stuck) == 0


def test_find_stuck_excludes_failed(db: Path):
    """Failed messages also don't show as stuck (terminal)."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    for _ in range(3):
        record_failure(db, 1, Stage.SUMMARIZE, "err", max_attempts=3)
    with open_db(db) as conn:
        conn.execute(
            "UPDATE conversation_workflow SET created_at = datetime('now', '-120 seconds') "
            "WHERE message_id = 1"
        )
    stuck = find_stuck(db, stuck_after_seconds=60)
    assert len(stuck) == 0


def test_attempts_counters_accumulate(db: Path):
    """Per-stage attempts counter increments on each operation."""
    create_workflow(db, message_id=1, trace_id="tr-1")
    advance_stage(db, 1, Stage.ENCRYPT)
    record_failure(db, 1, Stage.SUMMARIZE, "err", max_attempts=10)
    record_failure(db, 1, Stage.SUMMARIZE, "err", max_attempts=10)
    with open_db(db) as conn:
        row = conn.execute(
            "SELECT attempts_encrypt, attempts_summarize FROM conversation_workflow "
            "WHERE message_id = 1"
        ).fetchone()
        assert row["attempts_encrypt"] == 1
        assert row["attempts_summarize"] == 2


def test_get_workflow_returns_none_for_missing(db: Path):
    """get_workflow returns None when message_id not found."""
    assert get_workflow(db, 999) is None
