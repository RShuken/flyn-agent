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
