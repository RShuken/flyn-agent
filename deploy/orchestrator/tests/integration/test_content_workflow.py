# tests/integration/test_content_workflow.py
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.types import (
    InboundTaskRequest, TaskState, ApprovalDecision, WorkerRole,
)
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.worktree import WorktreeManager
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.workflows import load_workflow
from flyn_orchestrator.backends.base import WorkerResult


@pytest.fixture
def content_router(tmp_path, monkeypatch):
    content_wf = load_workflow(Path(__file__).parents[2] / "flyn_orchestrator" / "workflows" / "content.yaml")
    monkeypatch.setenv("FLYN_CONTENT_OUTPUT_ROOT", str(tmp_path / "out"))

    def _run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path); wt.mkdir(parents=True, exist_ok=True)
        cap = wt / f"{spec.worker_id}.jsonl"

        # Route on role enum
        if spec.role == WorkerRole.PM:
            body = {
                "title": "Test Email Draft", "platform": "email",
                "audience": "a Cora teammate", "tone": "friendly",
                "voice": "warm", "length_target": "short",
                "key_points": ["greet", "ask for info"],
                "needs_fact_check": False, "needs_humanize": False,
                "wants_send": False, "send_destination": "",
            }
            cap.write_text(json.dumps({"type":"result","result":json.dumps(body)}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[],
                summary=json.dumps(body),
            )
        elif spec.role == WorkerRole.WRITER and "humanize" not in spec.worker_id.lower():
            draft = "Hi there!\n\nQuick request — could you send over the latest numbers?\n\nThanks,\nFlyn"
            cap.write_text(json.dumps({"type":"result","result":draft}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=draft,
            )
        elif spec.role == WorkerRole.EDITOR:
            body = {"passed": True, "summary": "draft is clean", "edits": []}
            cap.write_text(json.dumps({"type":"result","result":json.dumps(body)}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[],
                summary=json.dumps(body),
            )
        else:
            # Humanizer (also WorkerRole.WRITER but worker_id contains "humanize")
            humanized = "Hey — got a quick ask. Can you share the latest numbers? Cheers"
            cap.write_text(json.dumps({"type":"result","result":humanized}))
            return WorkerResult(
                worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                cost_usd=0.01, duration_ms=10, changed_files=[], summary=humanized,
            )

    backend = MagicMock(); backend.name = "claude-p"; backend.run = _run
    dispatcher = WorkerDispatcher()
    dispatcher.register_backend("claude-p", backend)

    http = MagicMock(); http.post.return_value.status_code = 200
    memory = MemoryEmitter(router_url="http://localhost:8400", http=http)
    store = StateStore(db_path=tmp_path / "state.db")
    wt_mgr = WorktreeManager(workspaces_dir=tmp_path / "ws")

    router = TaskRouter(
        store=store, dispatcher=dispatcher, worktree_mgr=wt_mgr,
        memory=memory,
        repo_path_for_workflow=lambda w: tmp_path,
        builder_prompt_path=Path(__file__).parents[2] / "flyn_orchestrator" / "prompts" / "builder.md",
        workflows=[content_wf],
    )
    return router, store, tmp_path


def test_content_workflow_default_delivers_as_draft(content_router):
    """Default flow — wants_send=False — task → DELIVERABLE_READY with draft posted."""
    router, store, tmp_path = content_router
    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="draft a quick email to Beth asking for the latest numbers",
        external_message_id="msg-c-1",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.DELIVERABLE_READY
    payload = final.raw_payload or {}
    draft_path = Path(payload.get("draft_path", ""))
    assert draft_path.exists()
    text = draft_path.read_text()
    assert "Quick request" in text or "Hi there" in text


def test_content_workflow_blocks_on_editor_failure(content_router):
    """When the editor returns passed=False with a critical finding, task → CHANGES_REQUESTED."""
    router, store, tmp_path = content_router

    original_run = router._dispatcher._registry.get("claude-p").run
    def _editor_blocks(spec, prompt, *, cost_tracker=None):
        if spec.role == WorkerRole.EDITOR:
            body = {"passed": False, "summary": "factual error",
                    "edits": [{"severity": "critical", "type": "spec_mismatch",
                              "where": "para 1", "suggestion": "wrong recipient"}]}
            wt = Path(spec.worktree_path); wt.mkdir(parents=True, exist_ok=True)
            cap = wt / f"{spec.worker_id}.jsonl"
            cap.write_text(json.dumps({"type":"result","result":json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        return original_run(spec, prompt, cost_tracker=cost_tracker)
    router._dispatcher._registry.get("claude-p").run = _editor_blocks

    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="draft a thing",
        external_message_id="msg-c-blocked",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.CHANGES_REQUESTED


def test_content_workflow_send_flow_transitions_to_final_approval(content_router):
    """When PM sets wants_send=True with a destination, task → FINAL_APPROVAL_PENDING."""
    router, store, tmp_path = content_router

    original_run = router._dispatcher._registry.get("claude-p").run
    def _pm_wants_send(spec, prompt, *, cost_tracker=None):
        if spec.role == WorkerRole.PM:
            body = {
                "title": "Send to Beth", "platform": "telegram",
                "audience": "Beth", "tone": "friendly",
                "voice": "warm", "length_target": "short",
                "key_points": ["status update"],
                "needs_fact_check": False, "needs_humanize": False,
                "wants_send": True, "send_destination": "Beth on Telegram (chat_id 7434192034)",
            }
            wt = Path(spec.worktree_path); wt.mkdir(parents=True, exist_ok=True)
            cap = wt / f"{spec.worker_id}.jsonl"
            cap.write_text(json.dumps({"type":"result","result":json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        return original_run(spec, prompt, cost_tracker=cost_tracker)
    router._dispatcher._registry.get("claude-p").run = _pm_wants_send

    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="send Beth a quick status update",
        external_message_id="msg-c-send",
        workflow_override="content",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.FINAL_APPROVAL_PENDING
    # The draft is staged but not yet sent
