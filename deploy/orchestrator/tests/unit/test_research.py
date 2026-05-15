# tests/unit/test_research.py
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.research import (
    decompose_intent, run_researchers, critique, synthesize,
    write_output, PMPlan, CritiqueResult,
)
from flyn_orchestrator.citations import Citation, ResearcherOutput
from flyn_orchestrator.backends.base import WorkerResult


def _stub_backend(summary_text: str):
    b = MagicMock()
    b.name = "stub"

    def _run(spec, prompt, *, cost_tracker=None):
        cap = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text(json.dumps({"type": "result", "result": summary_text}) + "\n")
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.01, duration_ms=10, changed_files=[], summary=summary_text,
        )

    b.run = _run
    return b


def test_decompose_intent_parses_pm_output(tmp_path):
    pm_json = json.dumps({
        "title": "Postgres vs MySQL",
        "rationale": "Choosing a DB for the new Cora API",
        "sub_questions": [
            {"id": "Q1", "question": "Performance differences"},
            {"id": "Q2", "question": "Operational complexity"},
        ],
        "estimated_sources": "official docs + 2-3 industry blogs",
    })
    backend = _stub_backend(pm_json)
    plan = decompose_intent("compare postgres vs mysql for our API",
                            scratch_dir=tmp_path, backend=backend)
    assert plan.title == "Postgres vs MySQL"
    assert len(plan.sub_questions) == 2
    assert plan.sub_questions[0]["id"] == "Q1"


def test_decompose_intent_returns_none_on_garbage(tmp_path):
    backend = _stub_backend("not json")
    assert decompose_intent("anything", scratch_dir=tmp_path, backend=backend) is None


def test_run_researchers_dispatches_one_per_sub_question(tmp_path):
    """Each sub-question should spawn exactly one researcher worker."""
    plan = PMPlan(
        title="t", rationale="r",
        sub_questions=[{"id": "Q1", "question": "a"}, {"id": "Q2", "question": "b"}],
        estimated_sources="x",
    )
    backend = MagicMock()
    backend.name = "claude-p"
    calls = []

    def _run(spec, prompt, *, cost_tracker=None):
        calls.append(spec.worker_id)
        cap = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        cap.parent.mkdir(parents=True, exist_ok=True)
        # Return a valid researcher output
        q_id = spec.worker_id.split("-")[-1]  # Q1 or Q2
        out = json.dumps({
            "sub_question_id": q_id,
            "sub_question": "x",
            "answer": "answer to " + spec.worker_id,
            "citations": [{"url": "https://x.com", "title": "x", "claim": "y", "accessed_at": "2026-05-15"}],
            "confidence": "high",
            "open_questions": [],
        })
        cap.write_text(json.dumps({"type": "result", "result": out}))
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.01, duration_ms=10, changed_files=[], summary=out,
        )

    backend.run = _run

    outputs = run_researchers(plan, scratch_dir=tmp_path, backend=backend, max_parallel=2)
    assert len(outputs) == 2
    assert len(calls) == 2
    assert set(o.sub_question_id for o in outputs) == {"Q1", "Q2"}


def test_run_researchers_caps_at_max_parallel(tmp_path):
    """If plan has 6 sub-questions but max_parallel=4, only 4 researchers run."""
    plan = PMPlan(
        title="t", rationale="r",
        sub_questions=[{"id": f"Q{i}", "question": "a"} for i in range(1, 7)],
        estimated_sources="x",
    )
    backend = MagicMock()
    backend.name = "x"
    call_count = 0

    def _run(spec, prompt, *, cost_tracker=None):
        nonlocal call_count
        call_count += 1
        cap = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text(json.dumps({"type": "result", "result": json.dumps({
            "sub_question_id": "Qx", "sub_question": "", "answer": "",
            "citations": [], "confidence": "low", "open_questions": []})}))
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.0, duration_ms=10, changed_files=[], summary="",
        )

    backend.run = _run
    outputs = run_researchers(plan, scratch_dir=tmp_path, backend=backend, max_parallel=4)
    # Only the first 4 sub_questions should have been dispatched
    assert call_count == 4


def test_critique_parses_passed_result(tmp_path):
    plan = PMPlan(title="t", rationale="r", sub_questions=[{"id": "Q1", "question": "x"}], estimated_sources="x")
    outputs = [ResearcherOutput(
        sub_question_id="Q1", sub_question="x", answer="x",
        citations=[Citation(url="https://x.com", title="x", claim="x", accessed_at="2026-05-15")],
        confidence="high", open_questions=[],
    )]
    critic_json = json.dumps({
        "passed": True, "summary": "looks good", "findings": []
    })
    backend = _stub_backend(critic_json)
    result = critique(plan, outputs, scratch_dir=tmp_path, backend=backend)
    assert result.passed is True
    assert result.summary == "looks good"


def test_critique_blocks_on_critical_finding(tmp_path):
    plan = PMPlan(title="t", rationale="r", sub_questions=[{"id": "Q1", "question": "x"}], estimated_sources="x")
    outputs = [ResearcherOutput(
        sub_question_id="Q1", sub_question="x", answer="x",
        citations=[], confidence="low", open_questions=[],
    )]
    critic_json = json.dumps({
        "passed": False, "summary": "unsourced claim",
        "findings": [{"severity": "critical", "category": "unsourced",
                      "note": "claim X has no citation", "sub_question_id": "Q1"}]
    })
    backend = _stub_backend(critic_json)
    result = critique(plan, outputs, scratch_dir=tmp_path, backend=backend)
    assert result.passed is False
    assert any(f.severity == "critical" for f in result.findings)


def test_synthesize_returns_markdown(tmp_path):
    plan = PMPlan(title="The Title", rationale="rat", sub_questions=[{"id": "Q1", "question": "x"}], estimated_sources="x")
    outputs = [ResearcherOutput(
        sub_question_id="Q1", sub_question="x", answer="x",
        citations=[Citation(url="https://x.com", title="t", claim="c", accessed_at="2026-05-15")],
        confidence="high", open_questions=[],
    )]
    md = "# The Title\n\nSome synthesis.\n"
    backend = _stub_backend(md)
    result = synthesize(
        title=plan.title, requester="ryan", task_id="T-0042", rationale=plan.rationale,
        outputs=outputs, minor_findings=[], scratch_dir=tmp_path, backend=backend,
    )
    assert "# The Title" in result
    assert "Some synthesis" in result


def test_write_output_creates_report_and_raw_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYN_RESEARCH_OUTPUT_ROOT", str(tmp_path / "research"))
    outputs = [ResearcherOutput(
        sub_question_id="Q1", sub_question="x", answer="x",
        citations=[], confidence="high", open_questions=[],
    )]
    report_path = write_output(
        report_md="# Report\n\nContent.\n",
        outputs=outputs, title="My Topic", task_id="T-0001",
    )
    assert report_path.exists()
    assert "Content." in report_path.read_text()
    # Raw notes should also exist
    raw_dir = report_path.parent / "raw"
    assert raw_dir.exists()
    raws = list(raw_dir.glob("*Q1*.json"))
    assert len(raws) == 1


def test_write_output_slugifies_title(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYN_RESEARCH_OUTPUT_ROOT", str(tmp_path / "research"))
    p = write_output(report_md="# x\n", outputs=[], title="Postgres vs MySQL: Which Wins?",
                     task_id="T-0001")
    # Topic dir should be slug-safe
    assert "postgres-vs-mysql" in str(p).lower() or "postgres" in str(p).lower()
