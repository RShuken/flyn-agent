"""Rules layer of the meeting classifier."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import ProjectConfig  # noqa: E402
from meeting_classifier import classify_by_rules  # noqa: E402


def _proj(slug: str, emails: list[str], keywords: list[str]) -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug,
        "repo": {"path": "/tmp/x", "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": f"S{i}", "role": "x", "side": "client",
             "primary_channel": "email", "email": e}
            for i, e in enumerate(emails)
        ],
        "meeting_keywords": keywords,
    })


def test_attendee_email_match():
    projects = [
        _proj("openliteracy", ["sarah@ol.org", "rebecca@ol.org"], []),
        _proj("cora", ["maria@cora.io"], []),
    ]
    meeting = {
        "title": "Quick chat",
        "attendees": [{"name": "S", "email": "sarah@ol.org"}],
    }
    slug, conf, reason = classify_by_rules(meeting, projects)
    assert slug == "openliteracy"
    assert conf == "rule"
    assert "sarah@ol.org" in reason


def test_title_keyword_match():
    projects = [
        _proj("openliteracy", [], ["OpenLiteracy", "OL Sprint"]),
        _proj("cora", [], ["Cora"]),
    ]
    meeting = {
        "title": "OL Sprint 1 mid-check",
        "attendees": [{"name": "Random", "email": "x@example.com"}],
    }
    slug, conf, reason = classify_by_rules(meeting, projects)
    assert slug == "openliteracy"
    assert "OL Sprint" in reason


def test_no_rule_match_returns_none():
    projects = [
        _proj("openliteracy", ["sarah@ol.org"], ["OpenLiteracy"]),
    ]
    meeting = {
        "title": "Lunch with mom",
        "attendees": [{"name": "Mom", "email": "mom@example.com"}],
    }
    slug, conf, reason = classify_by_rules(meeting, projects)
    assert slug is None
    assert conf == "no-rule"


def test_email_match_is_case_insensitive():
    projects = [_proj("openliteracy", ["sarah@OL.org"], [])]
    meeting = {"title": "x", "attendees": [{"email": "SARAH@ol.org"}]}
    slug, _, _ = classify_by_rules(meeting, projects)
    assert slug == "openliteracy"
