"""WebhookPMAdapter unit tests.

All HTTP is stubbed — no live endpoints contacted.
"""
from __future__ import annotations

import pytest

from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str = "T-wh-1") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        workflow="ops",
        state=TaskState.INBOUND,
        sender_role="teammate",
        sender_identifier="beth@openclaw.io",
        intent="Run the weekly status report",
    )


def _capturing_http(calls: list):
    """Stub that records calls and returns 200."""
    class _Resp:
        @property
        def status_code(self):
            return 200
    def _http(**kwargs):
        calls.append(kwargs)
        return _Resp()
    return _http


def _raising_http(**kwargs):
    raise OSError("simulated webhook failure")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_task_posts_task_created_event():
    """create_task must POST {event: 'task_created', data: {...}} to target_url."""
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local/events", http=_capturing_http(calls))
    t = _make_task()
    adapter.create_task(t)
    assert len(calls) == 1
    body = calls[0]["json"]
    assert body["event"] == "task_created"
    assert body["data"]["task_id"] == "T-wh-1"
    assert body["data"]["workflow"] == "ops"
    assert "intent" in body["data"]
    assert "sender_identifier" in body["data"]
    assert "sender_role" in body["data"]


def test_create_task_posts_to_target_url():
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local/events", http=_capturing_http(calls))
    adapter.create_task(_make_task())
    assert calls[0]["url"] == "http://hook.local/events"
    assert calls[0]["method"] == "POST"


def test_create_task_returns_webhook_task_id():
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_capturing_http([]))
    eid = adapter.create_task(_make_task(task_id="T-99"))
    assert eid == "webhook-T-99"


def test_update_state_posts_state_changed_event():
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_capturing_http(calls))
    t = _make_task()
    adapter.update_state(t, TaskState.RUNNING)
    assert calls[0]["json"]["event"] == "state_changed"
    assert calls[0]["json"]["data"]["to_state"] == "running"
    assert calls[0]["json"]["data"]["task_id"] == t.task_id


def test_link_artifact_posts_artifact_linked_event():
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_capturing_http(calls))
    t = _make_task()
    artifact = {"url": "https://example.com/pr/42", "type": "pull_request"}
    adapter.link_artifact(t, artifact)
    body = calls[0]["json"]
    assert body["event"] == "artifact_linked"
    assert body["data"]["artifact"] == artifact


def test_comment_on_task_posts_comment_added_event():
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_capturing_http(calls))
    t = _make_task()
    adapter.comment_on_task(t, "Reviewer approved — LGTM.")
    body = calls[0]["json"]
    assert body["event"] == "comment_added"
    assert body["data"]["body"] == "Reviewer approved — LGTM."


def test_comment_truncated_to_5000_chars():
    """Body longer than 5000 chars must be truncated."""
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_capturing_http(calls))
    long_body = "x" * 10_000
    adapter.comment_on_task(_make_task(), long_body)
    assert len(calls[0]["json"]["data"]["body"]) == 5000


def test_secret_header_included_when_configured():
    """X-Flyn-Secret header must appear when secret is set."""
    calls: list = []
    adapter = WebhookPMAdapter(
        target_url="http://hook.local",
        secret="s3cr3t",
        http=_capturing_http(calls),
    )
    adapter.create_task(_make_task())
    headers = calls[0].get("headers", {})
    assert headers.get("X-Flyn-Secret") == "s3cr3t"


def test_no_secret_header_when_not_configured():
    """X-Flyn-Secret header must NOT appear when no secret is set."""
    calls: list = []
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_capturing_http(calls))
    adapter.create_task(_make_task())
    headers = calls[0].get("headers", {})
    assert "X-Flyn-Secret" not in headers


def test_http_failure_swallowed_on_create_task():
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_raising_http)
    eid = adapter.create_task(_make_task(task_id="T-fail"))
    assert eid == "webhook-T-fail"  # still returns synthetic id


def test_http_failure_swallowed_on_update_state():
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_raising_http)
    adapter.update_state(_make_task(), TaskState.COMPLETED)  # must not raise


def test_http_failure_swallowed_on_link_artifact():
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_raising_http)
    adapter.link_artifact(_make_task(), {"url": "x"})  # must not raise


def test_http_failure_swallowed_on_comment():
    adapter = WebhookPMAdapter(target_url="http://hook.local", http=_raising_http)
    adapter.comment_on_task(_make_task(), "comment")  # must not raise


def test_configured_false_when_no_target_url():
    adapter = WebhookPMAdapter(target_url="")
    assert adapter.configured is False


def test_configured_true_when_target_url_set():
    adapter = WebhookPMAdapter(target_url="http://hook.local")
    assert adapter.configured is True


def test_post_not_called_when_not_configured():
    """When target_url is empty, no HTTP call should be made."""
    calls: list = []
    adapter = WebhookPMAdapter(target_url="", http=_capturing_http(calls))
    adapter.update_state(_make_task(), TaskState.RUNNING)
    assert calls == [], "HTTP must not be called when not configured"
