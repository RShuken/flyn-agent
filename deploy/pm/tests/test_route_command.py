"""Test the /route command handler."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "wiki-backend"))

import meetings_db  # noqa: E402
import route_command  # noqa: E402


@pytest.fixture
def setup(tmp_path):
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.DB_PATH = Path(_tmpdb.name)
    meetings_db.init_db()
    conn = meetings_db._connect()
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, attendees, status) "
        "VALUES (?, ?, '[]', 'review')",
        ("m-route-1", "x"),
    )
    conn.close()
    state = tmp_path / "last-review-list.json"
    state.write_text(json.dumps([{"index": 1, "meeting_id": "m-route-1"}]))
    return state


def test_route_skip_marks_dropped(setup):
    res = route_command.handle("/route 1 skip", state_path=setup)
    assert res["ok"] is True
    conn = meetings_db._connect()
    status = conn.execute(
        "SELECT status FROM meetings WHERE meeting_id='m-route-1'"
    ).fetchone()[0]
    conn.close()
    assert status == "dropped"


def test_route_to_project_calls_router(setup):
    with patch("route_command.load_project") as lp, \
         patch("route_command.route_meeting_to_project",
               return_value={"commit_sha": "deadbeef", "target_rel": "x"}) as rm:
        lp.return_value = type("C", (), {"slug": "openliteracy"})()
        res = route_command.handle("/route 1 openliteracy", state_path=setup)
    assert res["ok"] is True
    assert "deadbeef" in res["reply"]
    rm.assert_called_once()


def test_unknown_index_errors(setup):
    res = route_command.handle("/route 99 openliteracy", state_path=setup)
    assert res["ok"] is False
    assert "index" in res["reply"].lower()


def test_bad_usage_errors(setup):
    res = route_command.handle("/route", state_path=setup)
    assert res["ok"] is False


def test_force_reroute_blocked_by_default(setup):
    # Pre-mark as routed
    conn = meetings_db._connect()
    conn.execute("UPDATE meetings SET status='routed' WHERE meeting_id='m-route-1'")
    conn.close()
    res = route_command.handle("/route 1 openliteracy", state_path=setup)
    assert res["ok"] is False
    assert "already" in res["reply"].lower()
