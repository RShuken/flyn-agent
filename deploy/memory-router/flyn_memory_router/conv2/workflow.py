"""Atomic workflow transition helpers for conv-tier 2.0.

Every state change happens in a single SQL statement so that partial
crashes never leave the workflow row inconsistent. Each helper:

1. Reads the current state
2. Validates the transition against state.ALLOWED_TRANSITIONS
3. Updates state + the relevant *_at timestamp + attempts counter in
   one atomic UPDATE...WHERE...
4. Returns the new state on success or raises on invalid input

The COMPLETE state is set by `try_complete()` which checks all four
*_at columns and atomically flips state if every stage finished.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schema import open_db
from .state import (
    ALLOWED_TRANSITIONS,
    IN_FLIGHT_STATES,
    Stage,
    TERMINAL_STATES,
    WorkflowState,
    can_transition,
    is_complete,
    next_state,
)


def _now() -> str:
    """ISO8601 UTC timestamp matching SQLite's datetime('now') format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class WorkflowRow:
    message_id: int
    state: WorkflowState
    trace_id: str
    created_at: str
    encrypted_at: Optional[str]
    indexed_at: Optional[str]
    summarized_at: Optional[str]
    promoted_at: Optional[str]
    completed_at: Optional[str]
    failed_at: Optional[str]
    last_error: Optional[str]
    last_error_stage: Optional[str]


_STAGE_TS_COLUMN = {
    Stage.ENCRYPT: "encrypted_at",
    Stage.INDEX: "indexed_at",
    Stage.SUMMARIZE: "summarized_at",
    Stage.PROMOTE: "promoted_at",
}

_STAGE_ATTEMPTS_COLUMN = {
    Stage.ENCRYPT: "attempts_encrypt",
    Stage.INDEX: "attempts_index",
    Stage.SUMMARIZE: "attempts_summarize",
    Stage.PROMOTE: "attempts_promote",
}


def create_workflow(
    db_path: Path,
    message_id: int,
    trace_id: str,
) -> WorkflowRow:
    """Insert a fresh workflow row in state=received. Idempotent."""
    now = _now()
    with open_db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conversation_workflow "
            "(message_id, state, trace_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (message_id, WorkflowState.RECEIVED.value, trace_id, now),
        )
        return get_workflow(db_path, message_id)  # type: ignore[return-value]


def get_workflow(db_path: Path, message_id: int) -> Optional[WorkflowRow]:
    with open_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM conversation_workflow WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return WorkflowRow(
            message_id=row["message_id"],
            state=WorkflowState(row["state"]),
            trace_id=row["trace_id"],
            created_at=row["created_at"],
            encrypted_at=row["encrypted_at"],
            indexed_at=row["indexed_at"],
            summarized_at=row["summarized_at"],
            promoted_at=row["promoted_at"],
            completed_at=row["completed_at"],
            failed_at=row["failed_at"],
            last_error=row["last_error"],
            last_error_stage=row["last_error_stage"],
        )


def advance_stage(
    db_path: Path,
    message_id: int,
    stage: Stage,
) -> WorkflowState:
    """Mark `stage` complete for this message in a single atomic SQL stmt.

    Updates the stage's *_at timestamp, sets state to the stage's
    canonical next-state (if not already past that point), and bumps
    attempts. Returns the resulting state.

    Replay-safe: re-running the same advance is idempotent because the
    UPDATE uses a `state IN (...)` filter against ALLOWED_TRANSITIONS.
    """
    ts_col = _STAGE_TS_COLUMN[stage]
    attempts_col = _STAGE_ATTEMPTS_COLUMN[stage]
    now = _now()

    # Compute allowed "from" states for this stage
    allowed_from: list[str] = []
    next_target: WorkflowState
    for from_set, to_state in ALLOWED_TRANSITIONS[stage].items():
        for s in from_set:
            allowed_from.append(s.value)
        next_target = to_state  # only one entry per stage in current design
    # Also allow re-running if already past (idempotency); just bump attempts
    # without changing state.
    placeholders = ",".join("?" for _ in allowed_from)

    with open_db(db_path) as conn:
        # Step 1: atomically advance state if currently in an allowed "from" state
        cur = conn.execute(
            f"UPDATE conversation_workflow "
            f"   SET state = ?, {ts_col} = COALESCE({ts_col}, ?), "
            f"       {attempts_col} = {attempts_col} + 1 "
            f" WHERE message_id = ? "
            f"   AND state IN ({placeholders})",
            (next_target.value, now, message_id, *allowed_from),
        )
        if cur.rowcount == 0:
            # Either message doesn't exist or state isn't in allowed_from.
            # If state is already past, just record idempotent re-run by
            # setting the timestamp if missing and bumping attempts.
            conn.execute(
                f"UPDATE conversation_workflow "
                f"   SET {ts_col} = COALESCE({ts_col}, ?), "
                f"       {attempts_col} = {attempts_col} + 1 "
                f" WHERE message_id = ?",
                (now, message_id),
            )

        # Step 2: try to flip to COMPLETE if encrypt+index+summarize are done.
        # Promote (Graphiti POST) is best-effort and async — Graphiti runs
        # synchronous LLM-based entity extraction that can take minutes.
        # Blocking COMPLETE on it would tie our e2e p99 to Graphiti's p99,
        # which is out of pipeline control. promoted_at still gets set
        # when Graphiti eventually returns, for observability + dedup.
        conn.execute(
            "UPDATE conversation_workflow "
            "   SET state = 'complete', "
            "       completed_at = COALESCE(completed_at, ?) "
            " WHERE message_id = ? "
            "   AND state NOT IN ('complete', 'failed') "
            "   AND encrypted_at IS NOT NULL "
            "   AND indexed_at IS NOT NULL "
            "   AND summarized_at IS NOT NULL",
            (now, message_id),
        )

        new_state_row = conn.execute(
            "SELECT state FROM conversation_workflow WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return WorkflowState(new_state_row["state"])


def record_failure(
    db_path: Path,
    message_id: int,
    stage: Stage,
    error_message: str,
    max_attempts: int = 3,
) -> WorkflowState:
    """Record a failure on `stage`. If attempts >= max, transition to FAILED.

    Returns the resulting state. Caller is responsible for re-enqueueing
    if attempts < max (i.e., not yet at FAILED).
    """
    attempts_col = _STAGE_ATTEMPTS_COLUMN[stage]
    now = _now()
    with open_db(db_path) as conn:
        # Bump attempts and record the error
        conn.execute(
            f"UPDATE conversation_workflow "
            f"   SET {attempts_col} = {attempts_col} + 1, "
            f"       last_error = ?, "
            f"       last_error_stage = ? "
            f" WHERE message_id = ?",
            (error_message, stage.value, message_id),
        )

        # Check if we've exhausted retries → FAILED terminal state
        row = conn.execute(
            f"SELECT {attempts_col} as attempts FROM conversation_workflow "
            f" WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row and row["attempts"] >= max_attempts:
            conn.execute(
                "UPDATE conversation_workflow "
                "   SET state = 'failed', failed_at = ? "
                " WHERE message_id = ? AND state NOT IN ('complete', 'failed')",
                (now, message_id),
            )

        new_state_row = conn.execute(
            "SELECT state FROM conversation_workflow WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return WorkflowState(new_state_row["state"])


def find_stuck(db_path: Path, stuck_after_seconds: int = 60) -> list[WorkflowRow]:
    """Return workflow rows that haven't reached terminal state in N seconds.

    Used by the /health endpoint to surface stuck messages. A `stuck > 0`
    is the operator alert that something needs attention.
    """
    with open_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_workflow "
            " WHERE state NOT IN ('complete', 'failed') "
            "   AND datetime(created_at) < datetime('now', ?) "
            " ORDER BY created_at",
            (f"-{stuck_after_seconds} seconds",),
        ).fetchall()
        return [
            WorkflowRow(
                message_id=r["message_id"],
                state=WorkflowState(r["state"]),
                trace_id=r["trace_id"],
                created_at=r["created_at"],
                encrypted_at=r["encrypted_at"],
                indexed_at=r["indexed_at"],
                summarized_at=r["summarized_at"],
                promoted_at=r["promoted_at"],
                completed_at=r["completed_at"],
                failed_at=r["failed_at"],
                last_error=r["last_error"],
                last_error_stage=r["last_error_stage"],
            )
            for r in rows
        ]
