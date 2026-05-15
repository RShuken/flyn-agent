"""LLM (claude -p) layer of the meeting classifier."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import ProjectConfig  # noqa: E402
from meeting_classifier import classify_by_llm  # noqa: E402


def _proj(slug: str) -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug.title(),
        "repo": {"path": "/tmp/x", "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": "S", "role": "x", "side": "client",
             "primary_channel": "email", "email": f"s@{slug}.example"},
        ],
    })


def _mock_claude(output: dict, returncode: int = 0):
    """Build a fake subprocess.run result."""
    class R:
        def __init__(self): self.returncode = returncode
        @property
        def stdout(self): return json.dumps({"result": json.dumps(output)})
        @property
        def stderr(self): return ""
    return R()


def test_llm_high_confidence_routes():
    projects = [_proj("openliteracy"), _proj("cora")]
    meeting = {"title": "Sync", "attendees": [], "notes_text": "OL pathways"}
    with patch("meeting_classifier.subprocess.run",
               return_value=_mock_claude(
                   {"project": "openliteracy", "confidence": 0.92,
                    "reason": "notes mention pathways"})):
        slug, conf, reason = classify_by_llm(meeting, projects)
    assert slug == "openliteracy"
    assert conf == "llm-high"


def test_llm_low_confidence_marks_low():
    projects = [_proj("openliteracy"), _proj("cora")]
    meeting = {"title": "Sync", "attendees": [], "notes_text": "..."}
    with patch("meeting_classifier.subprocess.run",
               return_value=_mock_claude(
                   {"project": "openliteracy", "confidence": 0.5,
                    "reason": "weak signal"})):
        slug, conf, _ = classify_by_llm(meeting, projects)
    assert slug == "openliteracy"
    assert conf == "llm-low"


def test_llm_null_project_returns_none():
    projects = [_proj("openliteracy")]
    meeting = {"title": "Brunch", "attendees": [], "notes_text": "..."}
    with patch("meeting_classifier.subprocess.run",
               return_value=_mock_claude(
                   {"project": None, "confidence": 0.1,
                    "reason": "looks personal"})):
        slug, _, _ = classify_by_llm(meeting, projects)
    assert slug is None


def test_llm_bad_json_falls_through():
    projects = [_proj("openliteracy")]
    meeting = {"title": "x", "attendees": [], "notes_text": ""}
    class R:
        returncode = 0
        stdout = '{"result": "not valid json {{"}'
        stderr = ""
    with patch("meeting_classifier.subprocess.run", return_value=R()):
        slug, conf, _ = classify_by_llm(meeting, projects)
    assert slug is None
    assert conf == "llm-error"


def test_llm_timeout_falls_through():
    import subprocess as sp
    projects = [_proj("openliteracy")]
    meeting = {"title": "x", "attendees": [], "notes_text": ""}
    def raise_timeout(*a, **kw):
        raise sp.TimeoutExpired(cmd="claude", timeout=60)
    with patch("meeting_classifier.subprocess.run", side_effect=raise_timeout):
        slug, conf, _ = classify_by_llm(meeting, projects)
    assert slug is None
    assert conf == "llm-error"
