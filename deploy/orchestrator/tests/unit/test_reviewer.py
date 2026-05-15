from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.reviewer import review, _extract_json
from flyn_orchestrator.backends.base import WorkerResult


def test_extract_json_fenced():
    text = "blah\n```json\n{\"passed\": true, \"summary\": \"ok\", \"findings\": []}\n```\nmore"
    obj = _extract_json(text)
    assert obj["passed"] is True


def test_extract_json_inline():
    text = "{\"passed\": false, \"summary\": \"bad\", \"findings\": []}"
    obj = _extract_json(text)
    assert obj["passed"] is False


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("not json at all") is None


def test_review_returns_passed_findings(tmp_path: Path):
    backend = MagicMock()
    cap = tmp_path / "cap.jsonl"
    cap.write_text("")
    backend.run.return_value = WorkerResult(
        worker_id="w-001-reviewer", exit_code=0, capture_path=cap,
        cost_usd=0.01, duration_ms=100, changed_files=[],
        summary='```json\n{"passed":true,"summary":"good","findings":[]}\n```',
    )
    rf = review(worker_id="w-001", requirements="add hello", diff="+ print(hi)",
                test_results="ok", worktree_path=str(tmp_path), backend=backend)
    assert rf.passed is True


def test_review_unparseable_marks_failed(tmp_path: Path):
    backend = MagicMock()
    cap = tmp_path / "cap.jsonl"
    cap.write_text("garbage output that has no JSON")
    backend.run.return_value = WorkerResult(
        worker_id="w-001-reviewer", exit_code=0, capture_path=cap,
        cost_usd=0.01, duration_ms=100, changed_files=[], summary="garbage",
    )
    rf = review(worker_id="w-001", requirements="x", diff="y",
                test_results="z", worktree_path=str(tmp_path), backend=backend)
    assert rf.passed is False
    assert any(f.severity == "critical" for f in rf.findings)


def test_review_empty_diff_short_circuits_to_critical(tmp_path: Path):
    backend = MagicMock()
    rf = review(worker_id="w-001", requirements="add hello", diff="",
                test_results="ok", worktree_path=str(tmp_path), backend=backend)
    assert rf.passed is False
    assert any(f.severity == "critical" for f in rf.findings)
    assert not backend.run.called


def test_review_whitespace_diff_short_circuits(tmp_path: Path):
    backend = MagicMock()
    rf = review(worker_id="w-001", requirements="add hello", diff="   \n  \n\t",
                test_results="ok", worktree_path=str(tmp_path), backend=backend)
    assert rf.passed is False
    assert any(f.severity == "critical" for f in rf.findings)
    assert not backend.run.called
