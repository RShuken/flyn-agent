"""Integration test: full happy-path task roundtrip through TaskRouter.

Uses:
  - A real git repo (tmp_path fixture)
  - A stub backend that writes hello.py and commits it
  - A stub reviewer that returns LGTM
  - Real StateStore (SQLite), WorktreeManager, MemoryEmitter (mocked HTTP)
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import (
    InboundTaskRequest,
    ReviewFindings,
    TaskState,
    WorkerSpec,
)
from flyn_orchestrator.worktree import WorktreeManager


@pytest.fixture
def test_repo(tmp_path: Path) -> Path:
    r = tmp_path / "test-repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return r


def test_happy_path_task_roundtrip(tmp_path: Path, test_repo: Path):
    # ------------------------------------------------------------------
    # Stub backend: writes hello.py into the worktree and commits it
    # ------------------------------------------------------------------
    stub_backend = MagicMock()

    def _run(spec: WorkerSpec, prompt: str) -> WorkerResult:
        wt = Path(spec.worktree_path)
        (wt / "hello.py").write_text('print("hi")\n')
        subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(wt), "commit", "-m", "add hello"],
            check=True,
            capture_output=True,
        )
        return WorkerResult(
            worker_id=spec.worker_id,
            exit_code=0,
            capture_path=wt / f"{spec.worker_id}.jsonl",
            cost_usd=0.01,
            duration_ms=50,
            changed_files=["hello.py"],
            summary="created hello.py",
        )

    stub_backend.run = _run
    stub_backend.name = "stub"

    # ------------------------------------------------------------------
    # Stub reviewer: always passes
    # ------------------------------------------------------------------
    def stub_review(**kw) -> ReviewFindings:
        return ReviewFindings(
            worker_id=kw["worker_id"] + "-reviewer",
            passed=True,
            summary="LGTM",
            findings=[],
        )

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------
    dispatcher = WorkerDispatcher()
    dispatcher.register_backend("claude-p", stub_backend)

    http = MagicMock()
    http.post.return_value.status_code = 200
    memory = MemoryEmitter(router_url="http://localhost:8400", http=http)

    store = StateStore(db_path=tmp_path / "state.db")
    wt_mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")

    router = TaskRouter(
        store=store,
        dispatcher=dispatcher,
        worktree_mgr=wt_mgr,
        memory=memory,
        repo_path_for_workflow=lambda w: test_repo,
        builder_prompt_path=Path(__file__).parents[2]
        / "flyn_orchestrator"
        / "prompts"
        / "builder.md",
        reviewer_invoker=stub_review,
    )

    # ------------------------------------------------------------------
    # Exercise
    # ------------------------------------------------------------------
    req = InboundTaskRequest(
        channel="test",
        sender_identifier="ryan",
        sender_role="owner",
        intent="add a hello.py",
        external_message_id="msg-1",
    )

    task_id = router.accept(req)
    assert task_id.startswith("T-"), f"expected T-NNNN, got {task_id!r}"

    final = router.run_task(task_id)
    assert final.state == TaskState.DELIVERABLE_READY, (
        f"expected DELIVERABLE_READY, got {final.state}"
    )

    # ------------------------------------------------------------------
    # Verify all expected state transitions were recorded
    # ------------------------------------------------------------------
    events = store.list_events(task_id)
    recorded_to_states = [e["to_state"] for e in events]

    expected_states = [
        "triaging",
        "routed",
        "decomposed",
        "dispatched",
        "running",
        "reviewed",
        "deliverable_ready",
    ]
    for expected in expected_states:
        assert expected in recorded_to_states, (
            f"missing transition to {expected!r}; recorded={recorded_to_states}"
        )

    # ------------------------------------------------------------------
    # Verify memory emitter fired at least 3 times
    # ------------------------------------------------------------------
    assert http.post.call_count >= 3, (
        f"expected >= 3 memory emits, got {http.post.call_count}"
    )

    # ------------------------------------------------------------------
    # Verify the worktree has hello.py
    # ------------------------------------------------------------------
    wt = tmp_path / "ws" / task_id
    assert wt.exists(), f"worktree dir not found: {wt}"
    assert (wt / "hello.py").exists(), f"hello.py not in worktree: {list(wt.iterdir())}"
