"""Verify adapters emit `adapter_swallowed_error` events when wired with a memory_emitter.

Closes the KNOWLEDGE/20 observability gap. Each adapter that performs real I/O
must surface its swallowed errors via the optional memory_emitter, while
keeping the never-raise contract.
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.adapters._observability import emit_swallowed_error
from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter
from flyn_orchestrator.adapters.channels.telegram import TelegramChannelAdapter
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState


def _task(task_id: str = "T-obs-1") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        workflow="dev",
        state=TaskState.INBOUND,
        sender_role="owner",
        sender_identifier="obs-runner",
        intent="observability test",
    )


def _make_emitter():
    m = MagicMock()
    m.emit = MagicMock()
    return m


def _raise_http(**kwargs):
    raise OSError("simulated network failure for observability test")


# ---------------------------------------------------------------------------
# emit_swallowed_error helper unit tests
# ---------------------------------------------------------------------------

def test_helper_noop_when_emitter_is_none():
    """Helper is a no-op when memory_emitter is None — doesn't raise."""
    emit_swallowed_error(None, "test-adapter", "test-method", OSError("boom"))


def test_helper_emits_correct_shape():
    """The emitted event has the documented shape: event_type, subject, body, dedup_key."""
    emitter = _make_emitter()
    emit_swallowed_error(emitter, "test-adapter", "test-method",
                          OSError("network down"), task_id="T-42")
    emitter.emit.assert_called_once()
    kwargs = emitter.emit.call_args.kwargs
    assert kwargs["event_type"] == "adapter_swallowed_error"
    assert kwargs["subject"] == "T-42"
    assert "test-adapter.test-method" in kwargs["body"]
    assert "OSError" in kwargs["body"]
    assert "network down" in kwargs["body"]
    assert "T-42" in kwargs["dedup_key"]
    assert kwargs["importance"] == "cool"


def test_helper_subject_falls_back_to_adapter_name_when_no_task_id():
    emitter = _make_emitter()
    emit_swallowed_error(emitter, "fallback-adapter", "m", OSError("x"))
    assert emitter.emit.call_args.kwargs["subject"] == "fallback-adapter"
    assert "no-task" in emitter.emit.call_args.kwargs["dedup_key"]


def test_helper_truncates_long_exception_messages():
    emitter = _make_emitter()
    long_msg = "x" * 1000
    emit_swallowed_error(emitter, "a", "m", OSError(long_msg))
    body = emitter.emit.call_args.kwargs["body"]
    # Body holds the truncated message (first 200 chars after the prefix)
    assert "x" * 200 in body
    assert len(body) < 400


def test_helper_swallows_broken_emitter():
    """A memory_emitter that itself raises must not break the helper."""
    bad = MagicMock()
    bad.emit = MagicMock(side_effect=RuntimeError("emitter broken"))
    # Must not raise
    emit_swallowed_error(bad, "a", "m", OSError("original"))


# ---------------------------------------------------------------------------
# OLWikiPMAdapter
# ---------------------------------------------------------------------------

def test_olwiki_emits_on_http_failure():
    emitter = _make_emitter()
    adapter = OLWikiPMAdapter(http=_raise_http, memory_emitter=emitter)
    result = adapter.create_task(_task())
    assert result.startswith("olwiki-stub-")
    emitter.emit.assert_called_once()
    assert emitter.emit.call_args.kwargs["event_type"] == "adapter_swallowed_error"
    body = emitter.emit.call_args.kwargs["body"]
    assert "olwiki.create_task" in body
    assert "OSError" in body


def test_olwiki_no_emit_on_success():
    """Happy path: no swallowed-error event."""
    emitter = _make_emitter()
    def http_ok(**kw):
        class R:
            def json(self): return {"id": 7}
        return R()
    adapter = OLWikiPMAdapter(http=http_ok, memory_emitter=emitter)
    adapter.create_task(_task())
    emitter.emit.assert_not_called()


def test_olwiki_no_emitter_still_works():
    """Backward compat: adapter constructed without memory_emitter still
    swallows HTTP errors silently (no exception)."""
    adapter = OLWikiPMAdapter(http=_raise_http)  # memory_emitter defaults None
    result = adapter.create_task(_task())
    assert result.startswith("olwiki-stub-")


# ---------------------------------------------------------------------------
# WebhookPMAdapter
# ---------------------------------------------------------------------------

def test_webhook_emits_on_http_failure_create_task():
    emitter = _make_emitter()
    adapter = WebhookPMAdapter(target_url="http://stub.local", http=_raise_http,
                                memory_emitter=emitter)
    adapter.create_task(_task())
    # event_type in the emit is "adapter_swallowed_error"; the body should
    # name the webhook's own event ("task_created")
    emitter.emit.assert_called_once()
    body = emitter.emit.call_args.kwargs["body"]
    assert "webhook.task_created" in body


def test_webhook_emits_on_all_method_failures():
    """Each PMAdapter method that calls _post should surface a swallowed event."""
    emitter = _make_emitter()
    adapter = WebhookPMAdapter(target_url="http://stub.local", http=_raise_http,
                                memory_emitter=emitter)
    adapter.update_state(_task(), TaskState.RUNNING)
    adapter.link_artifact(_task(), {"url": "https://x"})
    adapter.comment_on_task(_task(), "comment")
    # 3 separate emit calls
    assert emitter.emit.call_count == 3
    events = [c.kwargs["body"] for c in emitter.emit.call_args_list]
    assert any("state_changed" in b for b in events)
    assert any("artifact_linked" in b for b in events)
    assert any("comment_added" in b for b in events)


def test_webhook_no_emit_when_unconfigured():
    """When target_url is empty, _post no-ops without calling http — no
    swallowed event is appropriate."""
    emitter = _make_emitter()
    adapter = WebhookPMAdapter(target_url="", http=_raise_http, memory_emitter=emitter)
    adapter.create_task(_task())
    emitter.emit.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramChannelAdapter
# ---------------------------------------------------------------------------

def test_telegram_emits_on_send_failure(monkeypatch):
    """When urlopen raises mid-send, Telegram adapter surfaces it."""
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram.urllib.request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("net down")),
    )
    emitter = _make_emitter()
    adapter = TelegramChannelAdapter(bot_token="stub", memory_emitter=emitter)
    adapter.send("12345", "body")
    emitter.emit.assert_called_once()
    body = emitter.emit.call_args.kwargs["body"]
    assert "telegram.send" in body
    assert "OSError" in body


def test_telegram_no_emit_when_unconfigured(monkeypatch):
    """No token = no-op in send; no swallowed event.

    Note: TelegramChannelAdapter falls back to `_load_bot_token()` (reads
    from ~/.openclaw/openclaw.json) when bot_token=None, so we stub that
    helper here to ensure the adapter is genuinely unconfigured for the test.
    """
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram._load_bot_token",
        lambda: "",
    )
    emitter = _make_emitter()
    adapter = TelegramChannelAdapter(bot_token=None, memory_emitter=emitter)
    adapter.send("12345", "body")
    emitter.emit.assert_not_called()


# ---------------------------------------------------------------------------
# EmailChannelAdapter
# ---------------------------------------------------------------------------

def test_email_emits_on_smtp_failure():
    emitter = _make_emitter()
    def raise_smtp(**kw):
        raise OSError("smtp down")
    adapter = EmailChannelAdapter(
        config={"smtp_host": "x", "smtp_port": 587, "imap_host": "y",
                "imap_port": 993, "username": "u", "password": "p"},
        smtp_sender=raise_smtp,
        memory_emitter=emitter,
    )
    adapter.send("to@example.com", "body")
    emitter.emit.assert_called_once()
    body = emitter.emit.call_args.kwargs["body"]
    assert "email.send" in body
    assert "OSError" in body


def test_email_no_emit_when_unconfigured():
    """No config = send no-ops without calling sender — no swallowed event."""
    emitter = _make_emitter()
    adapter = EmailChannelAdapter(config=None, memory_emitter=emitter)
    adapter.send("to@example.com", "body")
    emitter.emit.assert_not_called()
