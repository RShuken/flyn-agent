# tests/unit/test_content.py
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.content import (
    spec_content, draft_content, edit_content, fact_check_content, humanize_content,
    ContentSpec, EditFinding, EditResult, FactCheckFinding, FactCheckResult,
)
from flyn_orchestrator.backends.base import WorkerResult


def _stub_backend(summary_text: str):
    b = MagicMock()
    b.name = "stub"
    def _run(spec, prompt, *, cost_tracker=None):
        cap = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text(json.dumps({"type":"result","result":summary_text}))
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.01, duration_ms=10, changed_files=[], summary=summary_text,
        )
    b.run = _run
    return b


def test_spec_content_parses_pm_output(tmp_path):
    pm_json = json.dumps({
        "title": "Sponsor outreach",
        "platform": "email",
        "audience": "potential sponsor at Boulder Roots",
        "tone": "professional",
        "voice": "warm but direct",
        "length_target": "short",
        "key_points": ["mention upcoming event", "ask for tiered sponsorship"],
        "needs_fact_check": True,
        "needs_humanize": False,
        "wants_send": False,
        "send_destination": "",
    })
    spec = spec_content("draft a sponsor outreach email", scratch_dir=tmp_path,
                         backend=_stub_backend(pm_json))
    assert spec is not None
    assert spec.title == "Sponsor outreach"
    assert spec.platform == "email"
    assert spec.needs_fact_check is True
    assert spec.wants_send is False


def test_spec_content_returns_none_on_garbage(tmp_path):
    spec = spec_content("anything", scratch_dir=tmp_path, backend=_stub_backend("not json"))
    assert spec is None


def test_draft_content_returns_writer_output(tmp_path):
    spec = ContentSpec(
        title="x", platform="telegram", audience="x", tone="friendly",
        voice="x", length_target="short", key_points=["x"],
        needs_fact_check=False, needs_humanize=False, wants_send=False,
        send_destination="",
    )
    draft = draft_content(spec, scratch_dir=tmp_path,
                          backend=_stub_backend("Here is the draft text."))
    assert "Here is the draft text" in draft


def test_edit_content_parses_editor_json(tmp_path):
    spec = ContentSpec(
        title="x", platform="telegram", audience="x", tone="friendly",
        voice="x", length_target="short", key_points=["x"],
        needs_fact_check=False, needs_humanize=False, wants_send=False,
        send_destination="",
    )
    editor_json = json.dumps({
        "passed": True, "summary": "good",
        "edits": [{"severity": "minor", "type": "tone",
                  "where": "line 1", "suggestion": "less formal"}]
    })
    result = edit_content(spec, "draft text", scratch_dir=tmp_path,
                          backend=_stub_backend(editor_json))
    assert result.passed is True
    assert len(result.edits) == 1
    assert result.edits[0].severity == "minor"


def test_edit_content_blocks_on_critical(tmp_path):
    spec = ContentSpec(
        title="x", platform="telegram", audience="x", tone="friendly",
        voice="x", length_target="short", key_points=["x"],
        needs_fact_check=False, needs_humanize=False, wants_send=False,
        send_destination="",
    )
    editor_json = json.dumps({
        "passed": False, "summary": "factual error",
        "edits": [{"severity": "critical", "type": "spec_mismatch",
                  "where": "para 2", "suggestion": "wrong recipient"}]
    })
    result = edit_content(spec, "draft", scratch_dir=tmp_path,
                          backend=_stub_backend(editor_json))
    assert result.passed is False


def test_fact_check_content_parses_findings(tmp_path):
    spec = ContentSpec(
        title="x", platform="email", audience="x", tone="friendly",
        voice="x", length_target="short", key_points=["x"],
        needs_fact_check=True, needs_humanize=False, wants_send=False,
        send_destination="",
    )
    fc_json = json.dumps({
        "passed": True, "summary": "all claims verified", "claims_checked": 3,
        "findings": [{"severity": "info", "claim": "Boulder is in Colorado",
                     "issue": "unsupported", "evidence": "common knowledge",
                     "suggestion": ""}]
    })
    result = fact_check_content(spec, "draft", scratch_dir=tmp_path,
                                 backend=_stub_backend(fc_json))
    assert result.passed is True
    assert result.claims_checked == 3


def test_fact_check_content_blocks_on_critical(tmp_path):
    spec = ContentSpec(
        title="x", platform="email", audience="x", tone="friendly",
        voice="x", length_target="short", key_points=["x"],
        needs_fact_check=True, needs_humanize=False, wants_send=False,
        send_destination="",
    )
    fc_json = json.dumps({
        "passed": False, "summary": "wrong date",
        "claims_checked": 2,
        "findings": [{"severity": "critical", "claim": "event is on Jan 1",
                     "issue": "wrong", "evidence": "actual date is Jan 15",
                     "suggestion": "change to Jan 15"}]
    })
    result = fact_check_content(spec, "draft", scratch_dir=tmp_path,
                                 backend=_stub_backend(fc_json))
    assert result.passed is False


def test_humanize_content_returns_humanizer_output(tmp_path):
    spec = ContentSpec(
        title="x", platform="tweet", audience="x", tone="punchy",
        voice="x", length_target="280",
        key_points=["x"],
        needs_fact_check=False, needs_humanize=True, wants_send=False,
        send_destination="",
    )
    humanized = humanize_content(spec, "AI-sounding text with lots of em-dashes.",
                                  scratch_dir=tmp_path,
                                  backend=_stub_backend("Snappier human version."))
    assert humanized == "Snappier human version."
