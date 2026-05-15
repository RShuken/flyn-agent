from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.walkthrough import (
    generate_walkthrough, _render_prompt, _extract_text_from_capture,
)
from flyn_orchestrator.backends.base import WorkerResult


def test_render_prompt_substitutes_placeholders():
    out = _render_prompt(pr_url="https://gh.com/x/y/pull/42",
                         diff="+x = 1\n-y = 2",
                         task_intent="add x")
    assert "https://gh.com/x/y/pull/42" in out
    assert "+x = 1" in out
    assert "add x" in out
    # No leftover placeholder markers
    assert "{PR_URL}" not in out
    assert "{DIFF}" not in out
    assert "{TASK_INTENT}" not in out


def test_extract_text_from_summary_field():
    txt = '{"type":"result","result":"Here is the walkthrough."}'
    assert _extract_text_from_capture(txt) == "Here is the walkthrough."


def test_extract_text_from_result_dict():
    txt = '{"type":"result","result":{"summary":"explained"}}'
    assert _extract_text_from_capture(txt) == "explained"


def test_extract_text_returns_none_on_no_result():
    assert _extract_text_from_capture('{"type":"message","content":"hi"}') is None
    assert _extract_text_from_capture("") is None


def test_generate_walkthrough_calls_backend_and_returns_summary(tmp_path):
    backend = MagicMock()
    cap = tmp_path / "cap.jsonl"
    cap.write_text('{"type":"result","result":"**What this PR does:** adds /healthz."}')
    backend.run.return_value = WorkerResult(
        worker_id="walkthrough-1", exit_code=0, capture_path=cap,
        cost_usd=0.05, duration_ms=200, changed_files=[],
        summary="**What this PR does:** adds /healthz.",
    )
    out = generate_walkthrough(
        pr_url="https://gh.com/x/y/pull/1",
        diff="+def healthz(): return {ok:True}",
        task_intent="add a healthz endpoint",
        backend=backend,
    )
    assert "adds /healthz" in out
    backend.run.assert_called_once()


def test_generate_walkthrough_falls_back_to_capture_when_summary_empty(tmp_path):
    backend = MagicMock()
    cap = tmp_path / "cap.jsonl"
    cap.write_text('{"type":"result","result":"from capture: works fine"}\n')
    backend.run.return_value = WorkerResult(
        worker_id="walkthrough-1", exit_code=0, capture_path=cap,
        cost_usd=0.05, duration_ms=100, changed_files=[],
        summary="",  # empty summary
    )
    out = generate_walkthrough(
        pr_url="https://gh.com/x/y/pull/1",
        diff="x", task_intent="x", backend=backend,
    )
    assert "from capture" in out


def test_generate_walkthrough_handles_no_output_gracefully(tmp_path):
    backend = MagicMock()
    cap = tmp_path / "empty.jsonl"
    cap.write_text("")
    backend.run.return_value = WorkerResult(
        worker_id="walkthrough-1", exit_code=0, capture_path=cap,
        cost_usd=0.0, duration_ms=10, changed_files=[], summary="",
    )
    out = generate_walkthrough(
        pr_url="https://gh.com/x/y/pull/1",
        diff="x", task_intent="x", backend=backend,
    )
    assert "failed" in out.lower()
