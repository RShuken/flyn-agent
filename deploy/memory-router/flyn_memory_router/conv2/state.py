"""Workflow state machine for conv-tier 2.0.

Every conversation message has a workflow row whose `state` advances
deterministically through encrypt/index/summarize/promote stages until
`complete`. All transitions are validated against this module's
WorkflowState enum + ALLOWED_TRANSITIONS table — invalid transitions
raise, which is what the property-based tests assert.
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class WorkflowState(str, Enum):
    """The full lifecycle of a conv-tier message."""

    RECEIVED = "received"
    ENCRYPTED = "encrypted"
    INDEXED = "indexed"
    SUMMARIZED = "summarized"
    PROMOTED = "promoted"
    COMPLETE = "complete"
    FAILED = "failed"


class Stage(str, Enum):
    """The four pipeline stages, each driven by an independent worker."""

    ENCRYPT = "encrypt"
    INDEX = "index"
    SUMMARIZE = "summarize"
    PROMOTE = "promote"


# Per-stage state transitions. Each stage maps from a set of valid
# "from" states to the resulting "to" state. The state machine is a DAG
# (no cycles) with a single COMPLETE absorbing state. FAILED is a sink
# from any non-terminal state when retries are exhausted.
# Linear pipeline: received → encrypted → indexed → summarized → promoted → complete.
# Concurrency comes from running multiple workers WITHIN a stage (configurable),
# not from running stages in parallel. This keeps the state machine simple +
# deterministic without sacrificing throughput.
ALLOWED_TRANSITIONS: dict[Stage, dict[FrozenSet[WorkflowState], WorkflowState]] = {
    Stage.ENCRYPT: {
        frozenset({WorkflowState.RECEIVED}): WorkflowState.ENCRYPTED,
    },
    Stage.INDEX: {
        frozenset({WorkflowState.ENCRYPTED}): WorkflowState.INDEXED,
    },
    Stage.SUMMARIZE: {
        frozenset({WorkflowState.INDEXED}): WorkflowState.SUMMARIZED,
    },
    Stage.PROMOTE: {
        frozenset({WorkflowState.SUMMARIZED}): WorkflowState.PROMOTED,
    },
}

# Terminal states — no further transitions allowed.
TERMINAL_STATES: FrozenSet[WorkflowState] = frozenset({
    WorkflowState.COMPLETE,
    WorkflowState.FAILED,
})

# States that count as "stuck" if they haven't advanced in > stuck_threshold.
# COMPLETE and FAILED are terminal so excluded; any others are in-flight.
IN_FLIGHT_STATES: FrozenSet[WorkflowState] = frozenset(
    s for s in WorkflowState if s not in TERMINAL_STATES
)


def can_transition(stage: Stage, from_state: WorkflowState) -> bool:
    """True iff `stage` may run when workflow is in `from_state`."""
    for allowed_set in ALLOWED_TRANSITIONS[stage]:
        if from_state in allowed_set:
            return True
    return False


def next_state(stage: Stage, from_state: WorkflowState) -> WorkflowState:
    """The state to transition to when `stage` succeeds from `from_state`.

    Raises ValueError if (stage, from_state) is not a valid transition.
    """
    for allowed_set, to_state in ALLOWED_TRANSITIONS[stage].items():
        if from_state in allowed_set:
            return to_state
    raise ValueError(
        f"Invalid transition: stage={stage.value} from_state={from_state.value}"
    )


def is_complete(
    encrypted_at: str | None,
    indexed_at: str | None,
    summarized_at: str | None,
    promoted_at: str | None,
) -> bool:
    """A workflow is COMPLETE only when all four stages have finished.

    Caller passes the four timestamp columns from the workflow row.
    None means the stage hasn't completed yet.
    """
    return all(ts is not None for ts in
               (encrypted_at, indexed_at, summarized_at, promoted_at))
