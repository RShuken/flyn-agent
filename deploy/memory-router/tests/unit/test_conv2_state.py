"""WorkflowState + Stage + ALLOWED_TRANSITIONS — pure state machine tests."""
from __future__ import annotations

import pytest

from flyn_memory_router.conv2.state import (
    ALLOWED_TRANSITIONS,
    IN_FLIGHT_STATES,
    Stage,
    TERMINAL_STATES,
    WorkflowState,
    can_transition,
    is_complete,
    next_state,
)


def test_all_workflow_states_present():
    """Every state from the design doc has an enum entry."""
    expected = {"received", "encrypted", "indexed", "summarized", "promoted",
                "complete", "failed"}
    assert {s.value for s in WorkflowState} == expected


def test_all_stages_present():
    """Four pipeline stages — no more, no fewer."""
    assert {s.value for s in Stage} == {"encrypt", "index", "summarize", "promote"}


def test_terminal_states():
    """COMPLETE and FAILED are the only terminal states."""
    assert TERMINAL_STATES == frozenset({WorkflowState.COMPLETE, WorkflowState.FAILED})


def test_in_flight_states_excludes_terminal():
    """In-flight is the complement of terminal — used for stuck detection."""
    assert WorkflowState.COMPLETE not in IN_FLIGHT_STATES
    assert WorkflowState.FAILED not in IN_FLIGHT_STATES
    assert WorkflowState.RECEIVED in IN_FLIGHT_STATES
    assert WorkflowState.SUMMARIZED in IN_FLIGHT_STATES


def test_encrypt_only_from_received():
    """Encrypt stage runs only from RECEIVED."""
    assert can_transition(Stage.ENCRYPT, WorkflowState.RECEIVED)
    assert not can_transition(Stage.ENCRYPT, WorkflowState.ENCRYPTED)
    assert not can_transition(Stage.ENCRYPT, WorkflowState.COMPLETE)
    assert next_state(Stage.ENCRYPT, WorkflowState.RECEIVED) == WorkflowState.ENCRYPTED


def test_index_runs_only_from_encrypted():
    """Linear pipeline: index runs after encrypt completes."""
    assert can_transition(Stage.INDEX, WorkflowState.ENCRYPTED)
    assert not can_transition(Stage.INDEX, WorkflowState.RECEIVED)
    assert next_state(Stage.INDEX, WorkflowState.ENCRYPTED) == WorkflowState.INDEXED


def test_summarize_runs_only_from_indexed():
    """Linear pipeline: summarize runs after index completes."""
    assert can_transition(Stage.SUMMARIZE, WorkflowState.INDEXED)
    assert not can_transition(Stage.SUMMARIZE, WorkflowState.ENCRYPTED)
    assert not can_transition(Stage.SUMMARIZE, WorkflowState.RECEIVED)


def test_promote_runs_only_from_summarized():
    """Linear pipeline: promote runs only after summarize completes."""
    assert can_transition(Stage.PROMOTE, WorkflowState.SUMMARIZED)
    assert not can_transition(Stage.PROMOTE, WorkflowState.INDEXED)
    assert not can_transition(Stage.PROMOTE, WorkflowState.RECEIVED)


def test_invalid_transition_raises():
    """next_state raises for invalid (stage, from_state) pairs."""
    with pytest.raises(ValueError, match="Invalid transition"):
        next_state(Stage.ENCRYPT, WorkflowState.COMPLETE)
    with pytest.raises(ValueError, match="Invalid transition"):
        next_state(Stage.SUMMARIZE, WorkflowState.RECEIVED)


def test_is_complete_requires_all_four_timestamps():
    """is_complete returns True only when all 4 *_at columns are set."""
    assert not is_complete(None, None, None, None)
    assert not is_complete("ts", None, None, None)
    assert not is_complete("ts", "ts", "ts", None)
    assert is_complete("a", "b", "c", "d")


# --- Property-based: every (stage, from_state) combo is well-defined ---
# This proves the state machine has no undefined behavior.

@pytest.mark.parametrize("stage", list(Stage))
@pytest.mark.parametrize("from_state", list(WorkflowState))
def test_every_transition_is_either_allowed_or_explicitly_invalid(
    stage: Stage, from_state: WorkflowState
):
    """For every (stage, from_state), can_transition returns a bool —
    never raises. If True, next_state returns a valid WorkflowState.
    If False, next_state raises ValueError. No undefined behavior."""
    allowed = can_transition(stage, from_state)
    if allowed:
        result = next_state(stage, from_state)
        assert isinstance(result, WorkflowState)
        # The resulting state must be different from the input (no self-loops)
        assert result != from_state
        # The resulting state must not be a terminal state set by stage transitions
        # (COMPLETE is set by the all-done check; FAILED by record_failure)
        assert result not in TERMINAL_STATES
    else:
        with pytest.raises(ValueError):
            next_state(stage, from_state)


def test_state_machine_is_a_dag():
    """No cyclical transitions: once you reach state X via stage Y,
    you cannot return to a state that produces X via the same stage."""
    # Walk all transitions, track which states each stage can produce.
    # The set of (from_state) → (to_state) edges must form a DAG.
    edges: set[tuple[WorkflowState, WorkflowState]] = set()
    for stage, transitions in ALLOWED_TRANSITIONS.items():
        for from_set, to_state in transitions.items():
            for from_state in from_set:
                edges.add((from_state, to_state))

    # Confirm there are no cycles by checking that no edge goes "backward"
    # in the canonical order. Build topological order.
    order = [
        WorkflowState.RECEIVED, WorkflowState.ENCRYPTED, WorkflowState.INDEXED,
        WorkflowState.SUMMARIZED, WorkflowState.PROMOTED,
    ]
    rank = {s: i for i, s in enumerate(order)}
    for src, dst in edges:
        # COMPLETE/FAILED aren't reached via stage transitions; skip them
        if dst in TERMINAL_STATES or src in TERMINAL_STATES:
            continue
        assert rank[dst] > rank[src], f"Backward edge {src} → {dst} breaks DAG"
