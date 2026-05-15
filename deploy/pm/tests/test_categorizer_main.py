"""End-to-end test of the nightly categorizer main loop (with mocks)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Set DB env BEFORE importing modules that read it.
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "wiki-backend"))

from _lib import ProjectConfig  # noqa: E402
import meetings_db  # noqa: E402
import meeting_categorizer as mcat  # noqa: E402


def _seed_meeting(conn, meeting_id: str, title: str, attendees: list):
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, attendees, status) "
        "VALUES (?, ?, ?, 'pending')",
        (meeting_id, title, json.dumps(attendees)),
    )


def _proj(slug: str, emails: list[str]) -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug,
        "repo": {"path": "/tmp/repo", "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": f"S{i}", "role": "x", "side": "client",
             "primary_channel": "email", "email": e}
            for i, e in enumerate(emails)
        ],
    })


@pytest.fixture
def fresh_db():
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    # Re-pin DB_PATH (test cross-module isolation pattern from earlier tasks)
    meetings_db.DB_PATH = Path(_tmpdb.name)
    meetings_db.init_db()
    return meetings_db._connect()


def test_rule_match_routes_meeting(fresh_db):
    _seed_meeting(fresh_db, "mtg-r1", "x",
                  [{"email": "sarah@ol.org"}])
    projects = [_proj("openliteracy", ["sarah@ol.org"])]

    with patch.object(mcat, "list_projects_for_classifier",
                      return_value=projects), \
         patch.object(mcat, "route_meeting_to_project",
                      return_value={"commit_sha": "abc", "target_rel": "x"}) as r:
        mcat.run_once()

    r.assert_called_once()
    row = fresh_db.execute(
        "SELECT status, routed_project FROM meetings WHERE meeting_id='mtg-r1'"
    ).fetchone()
    assert row[0] == "routed"
    assert row[1] == "openliteracy"


def test_unmatched_meeting_becomes_review(fresh_db):
    _seed_meeting(fresh_db, "mtg-u1", "Lunch",
                  [{"email": "mom@example.com"}])
    projects = [_proj("openliteracy", ["sarah@ol.org"])]

    with patch.object(mcat, "list_projects_for_classifier",
                      return_value=projects), \
         patch.object(mcat, "classify_by_llm",
                      return_value=(None, "llm-low", "weak signal")), \
         patch.object(mcat, "route_meeting_to_project") as r:
        mcat.run_once()

    r.assert_not_called()
    row = fresh_db.execute(
        "SELECT status FROM meetings WHERE meeting_id='mtg-u1'"
    ).fetchone()
    assert row[0] == "review"


def test_routing_failure_marks_error(fresh_db):
    _seed_meeting(fresh_db, "mtg-e1", "x",
                  [{"email": "sarah@ol.org"}])
    projects = [_proj("openliteracy", ["sarah@ol.org"])]

    with patch.object(mcat, "list_projects_for_classifier",
                      return_value=projects), \
         patch.object(mcat, "route_meeting_to_project",
                      side_effect=RuntimeError("git push failed")):
        mcat.run_once()

    row = fresh_db.execute(
        "SELECT status FROM meetings WHERE meeting_id='mtg-e1'"
    ).fetchone()
    assert row[0] == "error"


def test_stuck_classifying_rows_revert(fresh_db):
    fresh_db.execute(
        "INSERT INTO meetings (meeting_id, title, attendees, status, updated_at) "
        "VALUES (?, ?, '[]', 'classifying', datetime('now', '-2 hours'))",
        ("mtg-stuck", "x"),
    )
    with patch.object(mcat, "list_projects_for_classifier", return_value=[]), \
         patch.object(mcat, "classify_by_llm",
                      return_value=(None, "no-rule", "")):
        mcat.unstick_old_classifying()
    row = fresh_db.execute(
        "SELECT status FROM meetings WHERE meeting_id='mtg-stuck'"
    ).fetchone()
    assert row[0] == "pending"
