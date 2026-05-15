from datetime import datetime, timezone
from pathlib import Path
import pytest
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import TaskRecord, TaskState


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "state.db")


def _task(id="T-0001", workflow="dev") -> TaskRecord:
    return TaskRecord(
        task_id=id, workflow=workflow, state=TaskState.INBOUND,
        sender_role="owner", sender_identifier="ryan",
        intent="test intent " + id,
    )


def test_insert_then_get(store: StateStore):
    t = _task()
    store.insert_task(t)
    got = store.get_task(t.task_id)
    assert got is not None
    assert got.task_id == t.task_id


def test_get_missing_returns_none(store: StateStore):
    assert store.get_task("nonexistent") is None


def test_state_transition_records_event(store: StateStore):
    t = _task()
    store.insert_task(t)
    store.transition(t.task_id, TaskState.INBOUND, TaskState.TRIAGING,
                     actor="system", reason="auto-route")
    evs = store.list_events(t.task_id)
    assert len(evs) == 1
    assert evs[0]["to_state"] == "triaging"


def test_transition_is_idempotent(store: StateStore):
    t = _task()
    store.insert_task(t)
    store.transition(t.task_id, TaskState.INBOUND, TaskState.TRIAGING, actor="x", reason="r")
    store.transition(t.task_id, TaskState.INBOUND, TaskState.TRIAGING, actor="x", reason="r")
    evs = store.list_events(t.task_id)
    # second identical transition is a no-op
    assert len(evs) == 1


def test_next_task_id_increments(store: StateStore):
    a = store.next_task_id()
    b = store.next_task_id()
    assert a.startswith("T-") and b.startswith("T-")
    assert int(b[2:]) == int(a[2:]) + 1
