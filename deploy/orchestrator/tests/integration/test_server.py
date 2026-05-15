"""Integration tests for the FastAPI server. Uses TestClient + stubs (no real claude)."""
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from flyn_orchestrator.backends.base import WorkerResult
from flyn_orchestrator.dispatcher import WorkerDispatcher
from flyn_orchestrator.types import ReviewFindings


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=r, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=r, check=True, capture_output=True)
    return r


@pytest.fixture
def client(tmp_path: Path, repo: Path, monkeypatch):
    monkeypatch.setenv("FLYN_ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("FLYN_DEFAULT_TEST_REPO", str(repo))
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_URL", "http://localhost:9999")  # bogus — mocked

    from flyn_orchestrator.server import build_app

    # Stub backend
    dispatcher = WorkerDispatcher()
    def _run(spec, prompt, *, cost_tracker=None):
        wt = Path(spec.worktree_path)
        (wt / "hello.py").write_text('print("hi")\n')
        subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
        # Allow exit code 1 (nothing to commit) — idempotent re-runs from TestClient BackgroundTasks
        subprocess.run(["git", "-C", str(wt), "commit", "-m", "add hello"], capture_output=True)
        cap = wt / f"{spec.worker_id}.jsonl"
        cap.write_text('{"type":"message","content":"created hello.py"}\n' * 5)
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0,
            capture_path=cap,
            cost_usd=0.01, duration_ms=50,
            changed_files=["hello.py"], summary="created hello.py",
        )
    stub_backend = MagicMock()
    stub_backend.name = "claude-p"
    stub_backend.run = _run
    dispatcher.register_backend("claude-p", stub_backend)

    # Stub reviewer
    def stub_review(**kw):
        return ReviewFindings(
            worker_id=kw["worker_id"] + "-reviewer",
            passed=True, summary="LGTM", findings=[],
        )

    # Mock http for memory emitter
    http = MagicMock()
    http.post.return_value.status_code = 200

    app = build_app(http_client=http, dispatcher=dispatcher, reviewer_invoker=stub_review)
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "flyn-orchestrator"


def test_inbound_creates_task(client):
    r = client.post("/api/tasks/inbound", json={
        "channel": "manual", "sender_identifier": "ryan", "sender_role": "owner",
        "intent": "add hello.py", "external_message_id": "msg-1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"].startswith("T-")
    assert body["accepted"] is True
    # Get task should return the record
    r2 = client.get(f"/api/tasks/{body['task_id']}")
    assert r2.status_code == 200


def test_get_task_404_on_unknown(client):
    r = client.get("/api/tasks/T-9999")
    assert r.status_code == 404


def test_run_endpoint_executes_happy_path(client):
    r1 = client.post("/api/tasks/inbound", json={
        "channel": "manual", "sender_identifier": "ryan", "sender_role": "owner",
        "intent": "add hello.py", "external_message_id": "msg-run",
    })
    task_id = r1.json()["task_id"]
    # explicit run (background may have already run; this is idempotent because of state transitions)
    r2 = client.post(f"/api/tasks/{task_id}/run")
    # Either way, the task should be in DELIVERABLE_READY
    state = client.get(f"/api/tasks/{task_id}").json()["state"]
    assert state == "deliverable_ready", f"expected deliverable_ready, got {state}"


def test_cancel(client):
    r1 = client.post("/api/tasks/inbound", json={
        "channel": "manual", "sender_identifier": "ryan", "sender_role": "owner",
        "intent": "noop", "external_message_id": "msg-cancel",
    })
    task_id = r1.json()["task_id"]
    r2 = client.post(f"/api/tasks/{task_id}/cancel")
    assert r2.status_code == 200
