"""Tests for route_meeting_to_project() in _lib."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import route_meeting_to_project, ProjectConfig  # noqa: E402


@pytest.fixture
def fake_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKLOG.md").write_text("# WORKLOG\n")
    return repo


def _fake_cfg(repo_path: Path) -> ProjectConfig:
    return ProjectConfig(slug="testproj", raw={
        "display_name": "Test",
        "repo": {"path": str(repo_path), "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": "Ryan Shuken", "role": "dev", "side": "us",
             "primary_channel": "telegram", "chat_id": "7191564227"},
        ],
        "cadence": {"morning_standup": {"recipients": ["Ryan Shuken"]}},
    })


def test_route_writes_transcript_and_commits(fake_repo):
    meeting = {
        "meeting_id": "mtg-1",
        "title": "Sprint sync",
        "started_at": "2026-05-14T15:00:00Z",
        "attendees": [{"name": "Beth", "email": "beth@example.com"}],
        "transcript_text": "hello\nworld",
        "notes_text": None,
        "meeting_url": "https://krisp.ai/m/mtg-1",
    }
    cfg = _fake_cfg(fake_repo)

    with patch("_lib.git_pull") as pull, \
         patch("_lib.git_commit_and_push", return_value="abc1234") as push, \
         patch("_lib.graphiti_episode", return_value={"ok": True}) as graph, \
         patch("_lib.telegram_send") as tg:
        result = route_meeting_to_project(meeting, cfg)

    assert result["commit_sha"] == "abc1234"
    pull.assert_called_once()
    push.assert_called_once()
    graph.assert_called_once()
    assert tg.called  # at least one operator notified

    written = list(fake_repo.glob("docs/00-source/meetings/*/transcript.md"))
    assert len(written) == 1
    body = written[0].read_text()
    assert "hello" in body
    assert "beth@example.com" in body
