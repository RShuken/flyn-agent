import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock


# Add the bin directory to sys.path so we can import flyn-pr-nudge as a module
SCRIPT_PATH = Path(__file__).parents[2] / "bin" / "flyn-pr-nudge"


@pytest.fixture(autouse=True)
def _import_nudge():
    """Import the script as a module 'pr_nudge' for testing.

    Uses SourceFileLoader explicitly because the filename has no .py extension
    (hyphenated names aren't valid Python identifiers), so spec_from_file_location
    would return None.
    """
    import importlib.util
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader("pr_nudge", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("pr_nudge", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["pr_nudge"] = mod
    yield mod
    del sys.modules["pr_nudge"]


def test_task_id_from_branch_flyn(_import_nudge):
    assert _import_nudge.task_id_from_branch("flyn/T-0042") == "T-0042"
    assert _import_nudge.task_id_from_branch("flyn/T-0001") == "T-0001"


def test_task_id_from_branch_non_flyn(_import_nudge):
    assert _import_nudge.task_id_from_branch("main") is None
    assert _import_nudge.task_id_from_branch("feat/something") is None
    assert _import_nudge.task_id_from_branch("flyn-T-0001") is None  # missing slash


def test_is_stale_old_pr(_import_nudge, monkeypatch):
    fake_now = datetime(2026, 5, 15, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(_import_nudge, "_now", lambda: fake_now)
    pr = {"createdAt": (fake_now - timedelta(hours=72)).isoformat().replace("+00:00", "Z")}
    assert _import_nudge.is_stale(pr, threshold_hours=48) is True


def test_is_stale_recent_pr(_import_nudge, monkeypatch):
    fake_now = datetime(2026, 5, 15, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(_import_nudge, "_now", lambda: fake_now)
    pr = {"createdAt": (fake_now - timedelta(hours=24)).isoformat().replace("+00:00", "Z")}
    assert _import_nudge.is_stale(pr, threshold_hours=48) is False


def test_is_stale_malformed_date(_import_nudge):
    assert _import_nudge.is_stale({"createdAt": "not-a-date"}) is False
    assert _import_nudge.is_stale({}) is False


def test_lookup_task_returns_payload(_import_nudge, tmp_path):
    import sqlite3, json as _json
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, sender_identifier TEXT, sender_role TEXT, raw_payload TEXT)")
    conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)",
                 ("T-0042", "ryan@telegram", "owner",
                  _json.dumps({"chat_id": 7191564227, "thread_id": 100, "channel": "telegram"})))
    conn.commit(); conn.close()
    info = _import_nudge.lookup_task(db, "T-0042")
    assert info is not None
    assert info["sender_identifier"] == "ryan@telegram"
    assert info["raw_payload"]["chat_id"] == 7191564227
    assert info["raw_payload"]["thread_id"] == 100


def test_lookup_task_missing_returns_none(_import_nudge, tmp_path):
    import sqlite3
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, sender_identifier TEXT, sender_role TEXT, raw_payload TEXT)")
    conn.commit(); conn.close()
    assert _import_nudge.lookup_task(db, "T-NEVER") is None


def test_list_open_flyn_prs_runs_gh(_import_nudge, tmp_path, monkeypatch):
    fake_pr = [
        {"number": 7, "createdAt": "2026-05-13T00:00:00Z",
         "headRefName": "flyn/T-0007", "url": "https://gh.com/x/y/pull/7", "title": "Add healthz"},
    ]
    def fake_run(args, *a, **kw):
        m = MagicMock(); m.stdout = __import__("json").dumps(fake_pr); m.returncode = 0; m.stderr = ""
        return m
    monkeypatch.setattr(_import_nudge.subprocess, "run", fake_run)
    repo = tmp_path / "repo"; repo.mkdir()
    result = _import_nudge.list_open_flyn_prs(repo)
    assert len(result) == 1
    assert result[0]["number"] == 7


def test_list_open_flyn_prs_missing_repo_returns_empty(_import_nudge, tmp_path):
    assert _import_nudge.list_open_flyn_prs(tmp_path / "nonexistent") == []
