"""Tests for registry_parser's diff/sync logic.

The bug we are fixing: mode_diff used graphiti_search() to enumerate
existing episodes, but /api/search is semantic top-K, not enumeration.
So existing episodes got under-reported and mode_diff/--sync would
re-create duplicates. The fix swaps in graphiti_episodes_names() which
uses /api/episodes?group_id=X to actually enumerate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import registry_parser as rp  # noqa: E402
from _lib import ProjectConfig  # noqa: E402


def _q(qid: str) -> dict:
    """Minimal question shape registry_parser uses (id + text are what diff cares about)."""
    return {
        "id": qid,
        "section": qid[0],
        "section_title": "X",
        "text": f"Question {qid}",
        "ask": "",
        "bucket": "ai-does",
        "source": "S:1",
        "owner": "Beth",
        "status": "open",
        "depends_on": [],
        "target_sprint": None,
    }


def _cfg(slug: str = "openliteracy") -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug,
        "repo": {"path": "/tmp/unused-by-mocked-tests"},
        "source_of_truth": {"registry": "registry.md"},
    })


def test_missing_episode_questions_filters_existing():
    """The pure helper: given known-existing episode names, returns only
    questions whose ep name is NOT in that set."""
    questions = [_q("A.1"), _q("A.2"), _q("A.3")]
    existing = {"openliteracy-A.1", "openliteracy-A.3"}

    missing = rp._missing_episode_questions(questions, existing, slug="openliteracy")

    assert [q["id"] for q in missing] == ["A.2"]


def test_missing_episode_questions_all_missing_when_none_exist():
    questions = [_q("A.1"), _q("A.2")]
    missing = rp._missing_episode_questions(questions, set(), slug="openliteracy")
    assert [q["id"] for q in missing] == ["A.1", "A.2"]


def test_missing_episode_questions_none_missing_when_all_exist():
    questions = [_q("A.1"), _q("A.2")]
    existing = {"openliteracy-A.1", "openliteracy-A.2", "openliteracy-other"}
    missing = rp._missing_episode_questions(questions, existing, slug="openliteracy")
    assert missing == []


def test_mode_diff_uses_episodes_list_not_search(capfd):
    """mode_diff(apply=True) must call graphiti_episode only for the questions
    NOT already present per graphiti_episodes_names(). It must NOT consult
    graphiti_search() for existence (that endpoint returns top-K, not all)."""
    cfg = _cfg("openliteracy")
    questions = [_q("A.1"), _q("A.2"), _q("A.3")]
    # Existing episodes returned by the LIST endpoint:
    existing_names = {"openliteracy-A.1", "openliteracy-A.3"}

    posted_names: list[str] = []

    def fake_episode(body: str, name: str | None = None, **kwargs):
        posted_names.append(name)
        return {"ok": True}

    with patch.object(rp, "parse_registry", return_value=questions), \
         patch.object(rp, "graphiti_episodes_names", return_value=existing_names) as list_mock, \
         patch.object(rp, "graphiti_episode", side_effect=fake_episode), \
         patch.object(rp, "graphiti_search") as search_mock:
        rp.mode_diff(cfg, apply=True)

    # Only A.2 should have been posted.
    assert posted_names == ["openliteracy-A.2"]
    # And we should have hit the LIST endpoint, NOT the broken search path.
    list_mock.assert_called_once_with("openliteracy")
    search_mock.assert_not_called()
