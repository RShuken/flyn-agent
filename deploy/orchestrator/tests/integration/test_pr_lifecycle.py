"""Verify the dev-workflow router pushes a branch, opens a PR, and merges on approval.

Strategy for mocking:
- flyn_orchestrator.pr.create_pr and merge_pr are patched directly (they own gh calls).
  dev_phase imports pr via module namespace (`from . import pr as _pr`) so patches
  at flyn_orchestrator.pr.* still intercept after the Phase 2c extraction.
- subprocess.run is patched in BOTH flyn_orchestrator.router (used by _compute_diff)
  AND flyn_orchestrator.dev_phase (used by the git push step), with a selective
  side_effect that intercepts "git push" while forwarding everything else to the
  real subprocess.run.
- The stub backend uses _REAL_SUBPROCESS_RUN (captured before any patching) so its
  git add/commit calls are NOT affected by router/dev_phase patches.
"""
from __future__ import annotations

import subprocess as _subprocess_module
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Capture the real subprocess.run *before* any test-level patches are applied.
# We use os.popen or the C-level function to call the real subprocess even after
# mock.patch has replaced subprocess.run in the module namespace.
import subprocess as _sp
_REAL_SUBPROCESS_RUN = _sp.run

from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.types import (
    ApprovalDecision,
    InboundTaskRequest,
    ReviewFindings,
    TaskState,
)
from flyn_orchestrator.worktree import WorktreeManager
from flyn_orchestrator.workflows import load_workflow


def _make_selective_subprocess_mock(*, fail_push: bool = False):
    """Return a side_effect function for subprocess.run that:
    - intercepts 'git push' (returns success or raises CalledProcessError)
    - forwards all other calls to the real subprocess.run (captured before patching)
    """
    def _side_effect(args, *a, **kw):
        if isinstance(args, (list, tuple)) and len(args) >= 2:
            if args[0] == "git" and "push" in args:
                if fail_push:
                    raise _subprocess_module.CalledProcessError(
                        returncode=1,
                        cmd=list(args),
                        stderr="fatal: no upstream",
                    )
                return MagicMock(returncode=0, stdout="", stderr="")
        # Forward everything else to the real subprocess.run (pre-patch reference)
        return _REAL_SUBPROCESS_RUN(args, *a, **kw)
    return _side_effect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _REAL_SUBPROCESS_RUN(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    _REAL_SUBPROCESS_RUN(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    _REAL_SUBPROCESS_RUN(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("seed\n")
    _REAL_SUBPROCESS_RUN(["git", "add", "."], cwd=r, check=True)
    _REAL_SUBPROCESS_RUN(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return r


@pytest.fixture
def dev_router(tmp_path: Path, repo: Path):
    """Returns (router, store) wired for dev workflow with a stub backend."""
    dev_wf = load_workflow(
        Path(__file__).parents[2] / "flyn_orchestrator" / "workflows" / "dev.yaml"
    )

    # Stub backend: writes x.py, commits, creates capture file.
    # Uses _real_subprocess so these git calls are NOT intercepted by router patches.
    stub_backend = MagicMock()
    stub_backend.name = "claude-p"

    def _run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path)
        (wt / "x.py").write_text('print("x")\n')
        _REAL_SUBPROCESS_RUN(["git", "-C", str(wt), "add", "."], check=True)
        _REAL_SUBPROCESS_RUN(
            ["git", "-C", str(wt), "commit", "-m", "add x"],
            check=True,
            capture_output=True,
        )
        cap = wt / f"{spec.worker_id}.jsonl"
        # Each line ~30 bytes; need >= 100 bytes total for dispatcher's minimum check
        cap.write_text('{"type":"message","content":"x"}\n' * 5)
        return WorkerResult(
            worker_id=spec.worker_id,
            exit_code=0,
            capture_path=cap,
            cost_usd=0.01,
            duration_ms=50,
            changed_files=["x.py"],
            summary="ok",
        )

    stub_backend.run = _run

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
        repo_path_for_workflow=lambda w: repo,
        builder_prompt_path=Path(__file__).parents[2]
        / "flyn_orchestrator"
        / "prompts"
        / "builder.md",
        reviewer_invoker=lambda **kw: ReviewFindings(
            worker_id=kw["worker_id"] + "-reviewer",
            passed=True,
            summary="LGTM",
            findings=[],
        ),
        workflows=[dev_wf],
    )
    return router, store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("flyn_orchestrator.pr.create_pr")
@patch("flyn_orchestrator.dev_phase.subprocess.run")
@patch("flyn_orchestrator.router.subprocess.run")
def test_dev_workflow_pushes_and_opens_pr(mock_router_run, mock_dev_run, mock_create_pr, dev_router):
    """Happy path: dev workflow reaches FINAL_APPROVAL_PENDING with pr_url stored.

    subprocess.run is patched in both router and dev_phase with a selective
    side_effect: intercepts 'git push' (returns success), forwards all other
    calls to the real subprocess so that WorktreeManager and _compute_diff
    continue to work.
    """
    mock_router_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_dev_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_create_pr.return_value = "https://github.com/test/repo/pull/7"

    router, store = dev_router
    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryan",
        sender_role="owner",
        intent="please build a healthz endpoint",
        external_message_id="msg-dev-pr",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)

    assert final.state == TaskState.FINAL_APPROVAL_PENDING, (
        f"expected final_approval_pending, got {final.state}"
    )
    assert (final.raw_payload or {}).get("pr_url") == "https://github.com/test/repo/pull/7"
    assert (final.raw_payload or {}).get("branch") == f"flyn/{task_id}"

    # Verify create_pr was called
    mock_create_pr.assert_called_once()

    # Verify git push was intercepted (the selective mock captured it in dev_phase)
    push_calls = [
        call for call in mock_dev_run.call_args_list
        if call.args and isinstance(call.args[0], (list, tuple)) and "push" in call.args[0]
    ]
    assert len(push_calls) >= 1, "expected at least one git push call"


@patch("flyn_orchestrator.pr.create_pr")
@patch("flyn_orchestrator.dev_phase.subprocess.run")
@patch("flyn_orchestrator.router.subprocess.run")
def test_dev_workflow_falls_back_on_push_failure(mock_router_run, mock_dev_run, mock_create_pr, dev_router):
    """If git push fails, router falls back to DELIVERABLE_READY and never calls create_pr."""
    mock_router_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_dev_run.side_effect = _make_selective_subprocess_mock(fail_push=True)

    router, store = dev_router
    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryan",
        sender_role="owner",
        intent="please build a healthz endpoint",
        external_message_id="msg-push-fail",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)

    assert final.state == TaskState.DELIVERABLE_READY, (
        f"expected deliverable_ready fallback, got {final.state}"
    )
    mock_create_pr.assert_not_called()


@patch("flyn_orchestrator.pr.merge_pr")
@patch("flyn_orchestrator.pr.create_pr")
@patch("flyn_orchestrator.dev_phase.subprocess.run")
@patch("flyn_orchestrator.router.subprocess.run")
def test_approval_merges_pr(mock_router_run, mock_dev_run, mock_create_pr, mock_merge_pr, dev_router):
    """Approving a FINAL_APPROVAL_PENDING dev task calls merge_pr and transitions to COMPLETED."""
    mock_router_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_dev_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_create_pr.return_value = "https://github.com/test/repo/pull/42"
    mock_merge_pr.return_value = True

    router, store = dev_router
    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryan",
        sender_role="owner",
        intent="please build a thing",
        external_message_id="msg-merge",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.FINAL_APPROVAL_PENDING

    decision = ApprovalDecision(
        task_id=task_id,
        gate="human_approval",
        approver="ryan",
        approved=True,
    )
    updated = router.handle_approval(task_id, decision)
    assert updated.state == TaskState.COMPLETED, (
        f"expected completed, got {updated.state}"
    )
    mock_merge_pr.assert_called_once()


@patch("flyn_orchestrator.pr.merge_pr")
@patch("flyn_orchestrator.pr.create_pr")
@patch("flyn_orchestrator.dev_phase.subprocess.run")
@patch("flyn_orchestrator.router.subprocess.run")
def test_approval_rejection_cancels_task(mock_router_run, mock_dev_run, mock_create_pr, mock_merge_pr, dev_router):
    """Rejecting a FINAL_APPROVAL_PENDING dev task transitions to CANCELLED."""
    mock_router_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_dev_run.side_effect = _make_selective_subprocess_mock(fail_push=False)
    mock_create_pr.return_value = "https://github.com/test/repo/pull/99"
    mock_merge_pr.return_value = True  # should never be called

    router, store = dev_router
    req = InboundTaskRequest(
        channel="manual",
        sender_identifier="ryan",
        sender_role="owner",
        intent="please build a thing",
        external_message_id="msg-reject",
    )
    task_id = router.accept(req)
    router.run_task(task_id)

    decision = ApprovalDecision(
        task_id=task_id,
        gate="human_approval",
        approver="ryan",
        approved=False,
        reason="not needed anymore",
    )
    updated = router.handle_approval(task_id, decision)
    assert updated.state == TaskState.CANCELLED, (
        f"expected cancelled, got {updated.state}"
    )
    mock_merge_pr.assert_not_called()
