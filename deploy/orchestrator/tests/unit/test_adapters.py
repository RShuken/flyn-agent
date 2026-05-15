from typing import Optional
from unittest.mock import patch, MagicMock
import pytest

from flyn_orchestrator.adapters import (
    ChannelAdapter, NotifyAdapter, PMAdapter,
    ChannelRegistry, NotifyRegistry, PMRegistry,
)
from flyn_orchestrator.adapters.channels.telegram import TelegramChannelAdapter, _classify_sender, RYAN_CHAT_ID, BETH_CHAT_ID
from flyn_orchestrator.adapters.notify.stdout import StdoutNotifyAdapter
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter
from flyn_orchestrator.types import TaskRecord, TaskState


# ----- Registries -----

def test_channel_registry_round_trip():
    r = ChannelRegistry()
    a = TelegramChannelAdapter(bot_token="x")
    r.register(a)
    assert r.get("telegram") is a


def test_notify_registry_round_trip():
    r = NotifyRegistry()
    a = StdoutNotifyAdapter()
    r.register(a)
    assert r.get("stdout") is a


def test_pm_registry_round_trip():
    r = PMRegistry()
    a = LinearPMAdapter(api_key="x")
    r.register(a)
    assert r.get("linear") is a


def test_registry_missing_raises():
    with pytest.raises(KeyError):
        ChannelRegistry().get("nope")


# ----- Telegram -----

def test_telegram_ingest_parses_update():
    a = TelegramChannelAdapter(bot_token="x")
    raw = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "chat": {"id": RYAN_CHAT_ID, "type": "private"},
            "from": {"username": "ryan"},
            "text": "hello",
        },
    }
    req = a.ingest(raw)
    assert req is not None
    assert req.channel == "telegram"
    assert req.sender_role == "owner"
    assert req.intent == "hello"
    assert "tg-" in req.external_message_id


def test_telegram_ingest_bare_message():
    a = TelegramChannelAdapter(bot_token="x")
    msg = {
        "message_id": 7,
        "chat": {"id": BETH_CHAT_ID},
        "from": {"username": "beth"},
        "text": "kick off T-1",
    }
    req = a.ingest(msg)
    assert req.sender_role == "teammate"


def test_telegram_ingest_unknown_chat_is_other():
    a = TelegramChannelAdapter(bot_token="x")
    msg = {"message_id": 1, "chat": {"id": 999}, "from": {"username": "x"}, "text": "hi"}
    assert a.ingest(msg).sender_role == "other"


def test_telegram_ingest_missing_required_returns_none():
    a = TelegramChannelAdapter(bot_token="x")
    assert a.ingest({}) is None
    assert a.ingest({"message": {"chat": {"id": 1}}}) is None  # no text/message_id


def test_classify_sender():
    assert _classify_sender(RYAN_CHAT_ID) == "owner"
    assert _classify_sender(BETH_CHAT_ID) == "teammate"
    assert _classify_sender(0) == "other"


def test_telegram_send_no_token_is_silent():
    a = TelegramChannelAdapter(bot_token="")
    a.send("123", "hi")  # must not raise


def test_telegram_ingest_captures_thread_id():
    """If inbound message has message_thread_id, it must propagate to raw_payload."""
    a = TelegramChannelAdapter(bot_token="x")
    raw = {
        "update_id": 1,
        "message": {
            "message_id": 99,
            "chat": {"id": RYAN_CHAT_ID, "type": "supergroup", "is_forum": True},
            "from": {"username": "ryan"},
            "text": "build a healthz endpoint",
            "message_thread_id": 42,
        },
    }
    req = a.ingest(raw)
    assert req is not None
    assert req.raw_payload.get("thread_id") == 42


def test_telegram_topic_cache_load_and_save(tmp_path, monkeypatch):
    """The topic cache persists slug -> thread_id mapping to disk."""
    cache_file = tmp_path / "telegram_topics.json"
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram._TOPIC_CACHE_PATH",
        cache_file,
    )
    a = TelegramChannelAdapter(bot_token="x")
    a._save_topic("getcora", 100)
    a._save_topic("flyn-dev-sandbox", 101)
    # Reconstruct an adapter — should load from disk
    b = TelegramChannelAdapter(bot_token="x")
    assert b._get_topic_thread_id("getcora") == 100
    assert b._get_topic_thread_id("flyn-dev-sandbox") == 101
    assert b._get_topic_thread_id("unknown-slug") is None


def test_telegram_send_with_known_topic_uses_thread_id(monkeypatch, tmp_path):
    """When project_slug is given AND cached, send() posts with message_thread_id."""
    cache_file = tmp_path / "telegram_topics.json"
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram._TOPIC_CACHE_PATH",
        cache_file,
    )
    a = TelegramChannelAdapter(bot_token="test-token")
    a._save_topic("getcora", 100)

    captured_data: list[bytes] = []
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok":true}'
    def fake_urlopen(req, timeout=10):
        captured_data.append(req.data)
        return FakeResp()
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram.urllib.request.urlopen",
        fake_urlopen,
    )
    a.send(channel="-1001234567", body="hi from getcora", project_slug="getcora")
    assert captured_data, "urlopen was not called"
    payload = captured_data[0].decode()
    assert "message_thread_id=100" in payload or '"message_thread_id": 100' in payload or "message_thread_id=100" in payload.replace("&", " ")


def test_telegram_send_creates_topic_when_slug_unknown(monkeypatch, tmp_path):
    """When project_slug is given but NOT cached, send() first creates a topic via createForumTopic, caches the returned thread_id, then sends with it."""
    cache_file = tmp_path / "telegram_topics.json"
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram._TOPIC_CACHE_PATH",
        cache_file,
    )
    a = TelegramChannelAdapter(bot_token="test-token")

    call_log: list[str] = []
    class FakeResp:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self._body
    def fake_urlopen(req, timeout=10):
        url = req.full_url
        call_log.append(url)
        if "createForumTopic" in url:
            return FakeResp(b'{"ok":true,"result":{"message_thread_id":555,"name":"dev-newproj","icon_color":7322096}}')
        return FakeResp(b'{"ok":true}')
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram.urllib.request.urlopen",
        fake_urlopen,
    )
    a.send(channel="-1001234567", body="kickoff for new project", project_slug="newproj")
    # Should have made 2 calls: createForumTopic, then sendMessage
    assert any("createForumTopic" in c for c in call_log)
    assert any("sendMessage" in c for c in call_log)
    # And the topic should be cached now
    assert a._get_topic_thread_id("newproj") == 555


def test_telegram_send_without_slug_works_as_before(monkeypatch):
    """Backward compat: send(channel, body) without project_slug still works exactly like Phase 1b."""
    captured = []
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok":true}'
    def fake_urlopen(req, timeout=10):
        captured.append(req.full_url)
        return FakeResp()
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram.urllib.request.urlopen",
        fake_urlopen,
    )
    a = TelegramChannelAdapter(bot_token="test-token")
    a.send(channel="123", body="hello")
    assert captured  # sendMessage was called
    # No createForumTopic was called
    assert not any("createForumTopic" in c for c in captured)


# ----- Stdout notify -----

def test_stdout_notify_send(capsys):
    a = StdoutNotifyAdapter()
    a.send("task completed", "ryan")
    captured = capsys.readouterr()
    assert "task completed" in captured.out
    assert "ryan" in captured.out


# ----- Linear PM -----

def test_linear_create_task_stub():
    a = LinearPMAdapter(api_key=None)
    t = TaskRecord(task_id="T-1", workflow="dev", state=TaskState.INBOUND,
                   sender_role="owner", sender_identifier="x", intent="y")
    eid = a.create_task(t)
    assert eid == "linear-stub-T-1"


def test_linear_methods_no_op_when_unconfigured():
    with patch("flyn_orchestrator.adapters.pm.linear._load_linear_api_key", return_value=None):
        a = LinearPMAdapter(api_key=None)
    assert not a.configured
    t = TaskRecord(task_id="T-1", workflow="dev", state=TaskState.INBOUND,
                   sender_role="owner", sender_identifier="x", intent="y")
    # None of these raise
    a.update_state(t, TaskState.RUNNING)
    a.link_artifact(t, {"url": "https://x"})
    a.comment_on_task(t, "hi")
