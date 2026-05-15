"""Verify the router calls channel.send() at deliverable_ready."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock
import pytest

from flyn_orchestrator.types import (
    InboundTaskRequest, TaskState, ReviewFindings, WorkerSpec,
)
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.worktree import WorktreeManager
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.adapters import ChannelRegistry
from flyn_orchestrator.backends.base import WorkerResult


class _StubChannelAdapter:
    """Records send() calls for inspection. Implements ChannelAdapter Protocol structurally."""
    name = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def ingest(self, raw_message: dict[str, Any]) -> Optional[InboundTaskRequest]:
        return None

    def send(self, channel: str, body: str, attachments: Optional[list] = None) -> None:
        self.sent.append((channel, body))

    def approve_button(self, task_id: str, action: str) -> None:
        pass


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return r


def test_router_notifies_originating_channel_at_deliverable_ready(tmp_path: Path, repo: Path):
    stub_channel = _StubChannelAdapter()
    channels = ChannelRegistry()
    channels.register(stub_channel)

    # Stub backend that writes a real diff
    def _run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path)
        (wt / "hello.py").write_text('print("hi")\n')
        subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
        subprocess.run(["git", "-C", str(wt), "commit", "-m", "add hello"], check=True, capture_output=True)
        cap = wt / f"{spec.worker_id}.jsonl"
        cap.write_text('{"type":"message","content":"hi"}\n' * 5)  # > 100 bytes
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.01, duration_ms=50,
            changed_files=["hello.py"], summary="created hello.py",
        )
    stub_backend = MagicMock()
    stub_backend.name = "stub"
    stub_backend.run = _run

    dispatcher = WorkerDispatcher()
    dispatcher.register_backend("claude-p", stub_backend)

    def stub_review(**kw):
        return ReviewFindings(
            worker_id=kw["worker_id"] + "-reviewer",
            passed=True, summary="LGTM", findings=[],
        )

    http = MagicMock()
    http.post.return_value.status_code = 200
    memory = MemoryEmitter(router_url="http://localhost:8400", http=http)

    store = StateStore(db_path=tmp_path / "state.db")
    wt_mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")

    router = TaskRouter(
        store=store, dispatcher=dispatcher, worktree_mgr=wt_mgr,
        memory=memory,
        repo_path_for_workflow=lambda w: repo,
        builder_prompt_path=Path(__file__).parents[2] / "flyn_orchestrator" / "prompts" / "builder.md",
        reviewer_invoker=stub_review,
        channel_registry=channels,
    )

    # IMPORTANT: raw_payload must include channel + chat_id so the router knows where to send
    req = InboundTaskRequest(
        channel="telegram", sender_identifier="ryan@telegram", sender_role="owner",
        intent="add hello.py", external_message_id="msg-notify-1",
        raw_payload={"channel": "telegram", "chat_id": 7191564227},
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.DELIVERABLE_READY

    # Verify the channel was notified
    assert len(stub_channel.sent) >= 1, f"channel.send was not called: {stub_channel.sent}"
    chat_id, body = stub_channel.sent[-1]
    assert task_id in body, f"task_id not in body: {body!r}"
    assert "delivered" in body.lower() or "ready" in body.lower(), f"unexpected body: {body!r}"


def test_router_skips_notify_when_no_channel_in_payload(tmp_path: Path, repo: Path):
    """If raw_payload has no channel, router should not crash — just no notify."""
    stub_channel = _StubChannelAdapter()
    channels = ChannelRegistry()
    channels.register(stub_channel)

    def _run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path)
        (wt / "x.py").write_text("# x\n")
        subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
        subprocess.run(["git", "-C", str(wt), "commit", "-m", "x"], check=True, capture_output=True)
        cap = wt / f"{spec.worker_id}.jsonl"
        cap.write_text('{"type":"message","content":"x"}\n' * 5)  # > 100 bytes
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.0, duration_ms=10, changed_files=["x.py"], summary="ok",
        )
    stub_backend = MagicMock(); stub_backend.name = "stub"; stub_backend.run = _run

    dispatcher = WorkerDispatcher()
    dispatcher.register_backend("claude-p", stub_backend)

    http = MagicMock()
    memory = MemoryEmitter(router_url="http://localhost:8400", http=http)
    store = StateStore(db_path=tmp_path / "state.db")
    wt_mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")

    router = TaskRouter(
        store=store, dispatcher=dispatcher, worktree_mgr=wt_mgr,
        memory=memory,
        repo_path_for_workflow=lambda w: repo,
        builder_prompt_path=Path(__file__).parents[2] / "flyn_orchestrator" / "prompts" / "builder.md",
        reviewer_invoker=lambda **kw: ReviewFindings(
            worker_id=kw["worker_id"] + "-reviewer", passed=True, summary="ok", findings=[]),
        channel_registry=channels,
    )

    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="task", external_message_id="msg-no-channel",
        # NO raw_payload
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.DELIVERABLE_READY
    # No notify was attempted
    assert stub_channel.sent == []
