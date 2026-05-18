"""Integration tests for the research workflow router branch.

Task 4 — Phase 3: TaskRouter branches on workflow=='research'.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.types import (
    InboundTaskRequest, TaskState, WorkerSpec, WorkerRole,
)
from flyn_orchestrator.state import StateStore
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.worktree import WorktreeManager
from flyn_orchestrator.memory import MemoryEmitter
from flyn_orchestrator.router import TaskRouter
from flyn_orchestrator.workflows import load_workflow
from flyn_orchestrator.backends.base import WorkerResult


@pytest.fixture
def research_router(tmp_path, monkeypatch):
    research_wf = load_workflow(Path(__file__).parents[2] / "flyn_orchestrator" / "workflows" / "research.yaml")
    monkeypatch.setenv("FLYN_RESEARCH_OUTPUT_ROOT", str(tmp_path / "out"))

    # Stub backend that returns different JSON based on spec.role (unambiguous)
    def _run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path); wt.mkdir(parents=True, exist_ok=True)
        cap = wt / f"{spec.worker_id}.jsonl"

        if spec.role == WorkerRole.PM:
            body = {
                "title": "Test Research",
                "rationale": "test",
                "sub_questions": [
                    {"id": "Q1", "question": "first sub"},
                    {"id": "Q2", "question": "second sub"},
                ],
                "estimated_sources": "docs",
            }
            cap.write_text(json.dumps({"type": "result", "result": json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        elif spec.role == WorkerRole.RESEARCHER:
            # Determine sub_question_id from worker_id (e.g., "T-0001-researcher-Q1")
            q_id = "Q1" if spec.worker_id.endswith("-Q1") else "Q2"
            body = {
                "sub_question_id": q_id, "sub_question": "x",
                "answer": f"answer for {q_id}",
                "citations": [{"url": "https://anthropic.com", "title": "x",
                               "claim": "y", "accessed_at": "2026-05-15"}],
                "confidence": "high", "open_questions": [],
            }
            cap.write_text(json.dumps({"type": "result", "result": json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        elif spec.role == WorkerRole.CRITIC:
            body = {"passed": True, "summary": "looks clean", "findings": []}
            cap.write_text(json.dumps({"type": "result", "result": json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        else:
            # Synthesizer (WorkerRole.SYNTHESIZER)
            md = "# Test Research\n\nSynthesis here.\n\n## Q1\nAnswer 1.\n## Q2\nAnswer 2."
            cap.write_text(json.dumps({"type": "result", "result": md}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[], summary=md)

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
        repo_path_for_workflow=lambda w: tmp_path,    # not used for research
        builder_prompt_path=Path(__file__).parents[2] / "flyn_orchestrator" / "prompts" / "builder.md",
        workflows=[research_wf],
    )
    return router, store, tmp_path


def test_research_workflow_full_roundtrip(research_router):
    router, store, tmp_path = research_router
    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="research postgres vs mysql for our use case",  # matches "research" pattern
        external_message_id="msg-r-1",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.DELIVERABLE_READY
    payload = final.raw_payload or {}
    report_path = Path(payload.get("report_path", ""))
    assert report_path.exists()
    text = report_path.read_text()
    assert "Synthesis here" in text
    # Raw notes preserved
    raw_dir = report_path.parent / "raw"
    raws = list(raw_dir.glob("*.json"))
    assert len(raws) == 2


def test_research_workflow_blocks_on_critic_failure(research_router):
    """When the critic returns passed=False BOTH times (initial + retry), task → changes_requested.

    Phase 3b added auto-retry; if the retry also fails, we still reach CHANGES_REQUESTED.
    """
    router, store, tmp_path = research_router
    # Override the backend to make critic fail
    original_run = router._dispatcher._registry.get("claude-p").run

    def _run_critic_fails(spec, prompt, *, cost_tracker=None):
        if spec.role == WorkerRole.CRITIC:
            body = {"passed": False, "summary": "unsourced",
                    "findings": [{"severity": "critical", "category": "unsourced",
                                  "note": "claim X has no source", "sub_question_id": "Q1"}]}
            wt = Path(spec.worktree_path); wt.mkdir(parents=True, exist_ok=True)
            cap = wt / f"{spec.worker_id}.jsonl"
            cap.write_text(json.dumps({"type": "result", "result": json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        return original_run(spec, prompt, cost_tracker=cost_tracker)

    router._dispatcher._registry.get("claude-p").run = _run_critic_fails

    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="research foo",
        external_message_id="msg-r-blocked",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)
    assert final.state == TaskState.CHANGES_REQUESTED
    # Phase 3b: retry was attempted, retry_count=1 in payload
    payload = final.raw_payload or {}
    assert payload.get("research_retry_count") == 1
    assert payload.get("research_blocking_findings"), \
        f"Expected blocking findings in payload; got {payload}"


def test_research_workflow_retry_succeeds(research_router):
    """Critic fails first time, passes second time → DELIVERABLE_READY.

    Phase 3b: the auto-rerun should append the critic's findings as extra
    context to the researchers, and on second-pass the critic is satisfied.
    """
    router, store, tmp_path = research_router
    original_run = router._dispatcher._registry.get("claude-p").run
    critic_call_count = {"n": 0}
    researcher_prompts_seen: list[str] = []

    def _run_retry_succeeds(spec, prompt, *, cost_tracker=None):
        if spec.role == WorkerRole.RESEARCHER:
            researcher_prompts_seen.append(prompt)
        if spec.role == WorkerRole.CRITIC:
            critic_call_count["n"] += 1
            # First critic call fails, second passes
            if critic_call_count["n"] == 1:
                body = {"passed": False, "summary": "unsourced (initial)",
                        "findings": [{"severity": "critical", "category": "unsourced",
                                      "note": "claim X has no source", "sub_question_id": "Q1"}]}
            else:
                body = {"passed": True, "summary": "looks clean after retry",
                        "findings": []}
            wt = Path(spec.worktree_path); wt.mkdir(parents=True, exist_ok=True)
            cap = wt / f"{spec.worker_id}.jsonl"
            cap.write_text(json.dumps({"type": "result", "result": json.dumps(body)}))
            return WorkerResult(worker_id=spec.worker_id, exit_code=0, capture_path=cap,
                                cost_usd=0.01, duration_ms=10, changed_files=[],
                                summary=json.dumps(body))
        return original_run(spec, prompt, cost_tracker=cost_tracker)

    router._dispatcher._registry.get("claude-p").run = _run_retry_succeeds

    req = InboundTaskRequest(
        channel="manual", sender_identifier="ryan", sender_role="owner",
        intent="research with retry",
        external_message_id="msg-r-retry",
    )
    task_id = router.accept(req)
    final = router.run_task(task_id)

    assert final.state == TaskState.DELIVERABLE_READY, \
        f"Expected DELIVERABLE_READY after retry succeeded, got {final.state!r}"
    # Critic was called twice (first fail, second pass)
    assert critic_call_count["n"] == 2, \
        f"Expected 2 critic calls, got {critic_call_count['n']}"
    # The second batch of researcher prompts contains the critic findings as context
    # First batch: 2 prompts (Q1, Q2); retry batch: 2 more (Q1, Q2) = 4 total
    assert len(researcher_prompts_seen) == 4, \
        f"Expected 4 researcher prompts (2 + 2 retry), got {len(researcher_prompts_seen)}"
    retry_prompts = researcher_prompts_seen[2:]
    for p in retry_prompts:
        assert "Critic findings from previous research run" in p, \
            f"Retry prompt missing critic-findings context section"
        assert "claim X has no source" in p, \
            f"Retry prompt missing the specific blocking finding"
