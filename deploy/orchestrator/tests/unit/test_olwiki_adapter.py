"""OLWikiPMAdapter-specific unit tests.

All HTTP is stubbed — the live :8200 server is never contacted.
"""
from __future__ import annotations

import pytest

from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "T-wiki-1",
    intent: str = "Build a healthz endpoint",
    sender_identifier: str = "ryan@openclaw.io",
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        workflow="dev",
        state=TaskState.INBOUND,
        sender_role="owner",
        sender_identifier=sender_identifier,
        intent=intent,
    )


def _http_returning(payload: dict):
    """Return a stub HTTP callable that responds with *payload*."""
    class _Resp:
        def json(self):
            return payload
        @property
        def status_code(self):
            return 201
    def _http(**kwargs):
        return _Resp()
    return _http


def _http_raising(exc: Exception = OSError("network error")):
    """Return a stub HTTP callable that raises *exc*."""
    def _http(**kwargs):
        raise exc
    return _http


def _capturing_http(calls: list):
    """Return a stub that records every call and returns id=99."""
    class _Resp:
        def json(self):
            return {"id": 99}
        @property
        def status_code(self):
            return 201
    def _http(**kwargs):
        calls.append(kwargs)
        return _Resp()
    return _http


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_task_posts_to_decisions_endpoint():
    """create_task must POST to /api/decisions."""
    calls: list = []
    adapter = OLWikiPMAdapter(http=_capturing_http(calls))
    t = _make_task()
    adapter.create_task(t)
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/api/decisions")
    assert calls[0]["method"] == "POST"


def test_create_task_payload_shape():
    """Payload must include decided_by, summary, body_md, question_ids, source_meeting."""
    calls: list = []
    adapter = OLWikiPMAdapter(http=_capturing_http(calls))
    t = _make_task(intent="Launch the new feature", sender_identifier="beth@openclaw.io")
    adapter.create_task(t)
    payload = calls[0]["json"]
    assert payload["decided_by"] == "beth@openclaw.io"
    assert "Launch the new feature" in payload["summary"] or len(payload["summary"]) <= 300
    assert "T-wiki-1" in payload["body_md"]
    assert payload["question_ids"] == []
    assert payload["source_meeting"] is None


def test_create_task_returns_olwiki_decision_id_on_success():
    """When the API returns an id, the external_id must be 'olwiki-decision-<id>'."""
    adapter = OLWikiPMAdapter(http=_http_returning({"id": 77}))
    t = _make_task()
    eid = adapter.create_task(t)
    assert eid == "olwiki-decision-77"


def test_create_task_returns_stub_when_no_id_in_response():
    """When the API returns no 'id' key, fall back to stub id."""
    adapter = OLWikiPMAdapter(http=_http_returning({"status": "ok"}))
    t = _make_task(task_id="T-42")
    eid = adapter.create_task(t)
    assert eid == "olwiki-stub-T-42"


def test_create_task_returns_stub_on_http_error():
    """HTTP failure must not propagate — return stub id instead."""
    adapter = OLWikiPMAdapter(http=_http_raising())
    t = _make_task(task_id="T-err")
    eid = adapter.create_task(t)
    assert eid == "olwiki-stub-T-err"


def test_create_task_returns_stub_on_timeout():
    """Timeout (also an OSError subclass) is swallowed and returns stub id."""
    import socket
    adapter = OLWikiPMAdapter(http=_http_raising(TimeoutError("timed out")))
    t = _make_task(task_id="T-timeout")
    eid = adapter.create_task(t)
    assert eid == "olwiki-stub-T-timeout"


def test_summary_truncated_to_300_chars():
    """The summary field must be at most 300 characters."""
    long_intent = "x" * 500
    calls: list = []
    adapter = OLWikiPMAdapter(http=_capturing_http(calls))
    t = _make_task(intent=long_intent)
    adapter.create_task(t)
    assert len(calls[0]["json"]["summary"]) <= 300


def test_update_state_is_noop():
    adapter = OLWikiPMAdapter(http=_http_raising())
    t = _make_task()
    result = adapter.update_state(t, TaskState.RUNNING)
    assert result is None


def test_link_artifact_is_noop():
    adapter = OLWikiPMAdapter(http=_http_raising())
    t = _make_task()
    result = adapter.link_artifact(t, {"url": "https://x"})
    assert result is None


def test_comment_on_task_is_noop():
    adapter = OLWikiPMAdapter(http=_http_raising())
    t = _make_task()
    result = adapter.comment_on_task(t, "some comment")
    assert result is None


def test_configured_true_when_base_url_set():
    adapter = OLWikiPMAdapter(base_url="http://127.0.0.1:8200")
    assert adapter.configured is True


def test_base_url_trailing_slash_stripped():
    """Trailing slashes on base_url must not produce double-slash URLs."""
    calls: list = []
    adapter = OLWikiPMAdapter(base_url="http://127.0.0.1:8200/", http=_capturing_http(calls))
    adapter.create_task(_make_task())
    url = calls[0]["url"]
    assert "//" not in url.replace("http://", "").replace("https://", "")
