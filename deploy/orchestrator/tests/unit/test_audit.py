import json
from pathlib import Path
import pytest
from flyn_orchestrator.audit import (
    snapshot_target, verify_target_changed, serialize_snapshot, SnapshotBundle,
)
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import TaskRecord, TaskState


# ---------- Snapshot helpers ----------

def test_snapshot_existing_file_returns_hash(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello world")
    b = snapshot_target(str(f))
    assert b.kind == "file"
    assert b.hash_value != ""
    assert "size=11" in b.content_repr


def test_snapshot_missing_file_returns_sentinel(tmp_path):
    b = snapshot_target(str(tmp_path / "nope.txt"))
    assert b.kind == "file"
    assert "does not exist" in b.content_repr


def test_snapshot_unrecognized_target(tmp_path):
    b = snapshot_target("just_a_word")
    assert b.kind == "unsnapshottable"
    assert b.hash_value == ""
    assert "unrecognized" in (b.note or "").lower()


def test_verify_target_changed_detects_diff(tmp_path):
    before = SnapshotBundle(target="x", kind="file", hash_value="abc",
                              content_repr="", captured_at="2026-05-15")
    after = SnapshotBundle(target="x", kind="file", hash_value="xyz",
                             content_repr="", captured_at="2026-05-15")
    assert verify_target_changed(before, after) is True


def test_verify_target_changed_same_hash_is_unchanged():
    a = SnapshotBundle(target="x", kind="file", hash_value="abc",
                         content_repr="", captured_at="2026-05-15")
    b = SnapshotBundle(target="x", kind="file", hash_value="abc",
                         content_repr="", captured_at="2026-05-15")
    assert verify_target_changed(a, b) is False


def test_verify_target_changed_unsnapshottable_to_content_is_change():
    """If before couldn't snapshot but after has content, treat as changed."""
    a = SnapshotBundle(target="x", kind="unsnapshottable", hash_value="",
                         content_repr="", captured_at="2026-05-15")
    b = SnapshotBundle(target="x", kind="file", hash_value="xyz",
                         content_repr="", captured_at="2026-05-15")
    assert verify_target_changed(a, b) is True


def test_serialize_snapshot_returns_json(tmp_path):
    b = SnapshotBundle(target="/tmp/x", kind="file",
                         hash_value="a" * 64, content_repr="size=5",
                         captured_at="2026-05-15T12:00Z")
    out = serialize_snapshot(b)
    parsed = json.loads(out)
    assert parsed["target"] == "/tmp/x"
    assert parsed["kind"] == "file"
    assert "..." in parsed["hash"]


# ---------- Audit log via StateStore ----------

@pytest.fixture
def store(tmp_path):
    return StateStore(db_path=tmp_path / "state.db")


def test_append_audit_inserts_row(store):
    t = TaskRecord(
        task_id="T-1", workflow="ops", state=TaskState.INBOUND,
        sender_role="owner", sender_identifier="ryan", intent="rotate token",
    )
    store.insert_task(t)
    rid = store.append_audit(
        task_id="T-1", actor="executor", action="execute",
        target="/tmp/token.txt", before_hash="abc", after_hash="xyz",
        payload={"mode": "execute"},
    )
    assert rid > 0
    rows = store.list_audit("T-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "execute"
    assert rows[0]["before_hash"] == "abc"
    assert rows[0]["payload"]["mode"] == "execute"


def test_append_audit_multiple_rows_ordered(store):
    t = TaskRecord(
        task_id="T-1", workflow="ops", state=TaskState.INBOUND,
        sender_role="owner", sender_identifier="ryan", intent="rotate token",
    )
    store.insert_task(t)
    # Small sleep to ensure distinct timestamps
    import time
    store.append_audit(task_id="T-1", actor="executor", action="snapshot_before",
                       target="/tmp/x", payload={})
    time.sleep(0.01)
    store.append_audit(task_id="T-1", actor="executor", action="execute",
                       target="/tmp/x", payload={})
    time.sleep(0.01)
    store.append_audit(task_id="T-1", actor="executor", action="snapshot_after",
                       target="/tmp/x", payload={})
    rows = store.list_audit("T-1")
    assert [r["action"] for r in rows] == ["snapshot_before", "execute", "snapshot_after"]
