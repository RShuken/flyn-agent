"""Parametrized ChannelAdapter contract conformance suite.

Every class that claims to be a ChannelAdapter must satisfy this suite.
Currently covers: TelegramChannelAdapter, EmailChannelAdapter.

Rules being verified:
  - isinstance check against ChannelAdapter Protocol (runtime_checkable)
  - non-empty ``name`` attribute (str)
  - ``ingest`` with a valid raw_message returns InboundTaskRequest (or None per
    adapter-specific filters — never raises)
  - ``ingest`` with a malformed/empty raw_message returns None (never raises)
  - ``send`` returns None and does not raise — even when the adapter is
    unconfigured (no token/config) or when the underlying transport would fail
  - ``approve_button`` returns None and does not raise

These mirror the PMAdapter conformance suite at
``test_pm_adapter_conformance.py`` so the two adapter Protocols share the
same defensive guarantees.
"""
from __future__ import annotations

import pytest

from flyn_orchestrator.adapters.base import ChannelAdapter
from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter
from flyn_orchestrator.adapters.channels.telegram import TelegramChannelAdapter


# ---------------------------------------------------------------------------
# Per-adapter "valid raw_message" payloads
# ---------------------------------------------------------------------------
# Each adapter has its own raw_message shape. The conformance fixture pairs
# each adapter with a representative valid payload so ingest() can be exercised
# uniformly.

_TELEGRAM_VALID = {
    "update_id": 1,
    "message": {
        "message_id": 42,
        "from": {"username": "ryanshuken"},
        "chat": {"id": 7191564227},   # the allowlisted owner chat_id
        "text": "test from conformance suite",
    },
}

_EMAIL_VALID = {
    "from": "ryanshuken@gmail.com",   # in DEFAULT_ALLOWLIST → bypasses SPF/DKIM
    "subject": "[FLYN-TASK] conformance test",
    "body": "this is a clean body with no injection patterns",
    "headers": {},
    "message_id": "<conf-test-1@example.com>",
}

_TELEGRAM_INVALID = {"message": {}}    # missing chat_id, text, message_id
_EMAIL_INVALID = {"from": ""}          # no sender


# ---------------------------------------------------------------------------
# Parametrized fixture — (adapter_instance, valid_message, invalid_message)
# ---------------------------------------------------------------------------

@pytest.fixture(
    params=[
        pytest.param(
            ("telegram", lambda: TelegramChannelAdapter(bot_token=None), _TELEGRAM_VALID, _TELEGRAM_INVALID),
            id="telegram",
        ),
        pytest.param(
            ("email", lambda: EmailChannelAdapter(config=None), _EMAIL_VALID, _EMAIL_INVALID),
            id="email",
        ),
    ]
)
def channel_adapter(request):
    name, factory, valid, invalid = request.param
    return name, factory(), valid, invalid


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_adapter_implements_protocol(channel_adapter):
    name, adapter, *_ = channel_adapter
    assert isinstance(adapter, ChannelAdapter), (
        f"{name!r} does not satisfy ChannelAdapter structural protocol"
    )


def test_adapter_has_nonempty_name(channel_adapter):
    name, adapter, *_ = channel_adapter
    assert isinstance(adapter.name, str), f"{name}: 'name' must be a str"
    assert adapter.name, f"{name}: 'name' must be non-empty"


def test_ingest_valid_returns_request_or_none(channel_adapter):
    """A valid raw_message either returns an InboundTaskRequest (when filters
    pass) or None. Never raises."""
    from flyn_orchestrator.types import InboundTaskRequest
    name, adapter, valid, _ = channel_adapter
    result = adapter.ingest(valid)
    # Allow None (adapter may have filters that reject even valid-looking input).
    # The contract is: never raise; return None or InboundTaskRequest.
    assert result is None or isinstance(result, InboundTaskRequest), (
        f"{name}: ingest must return None or InboundTaskRequest, got {type(result)}"
    )


def test_ingest_invalid_returns_none(channel_adapter):
    """A malformed/empty raw_message must return None, never raise."""
    name, adapter, _, invalid = channel_adapter
    result = adapter.ingest(invalid)
    assert result is None, (
        f"{name}: ingest({invalid!r}) must return None on malformed input, got {result!r}"
    )


def test_ingest_empty_dict_returns_none(channel_adapter):
    """Edge case: completely empty dict. Must not raise; must return None."""
    name, adapter, *_ = channel_adapter
    result = adapter.ingest({})
    assert result is None, f"{name}: ingest({{}}) must return None"


def test_send_returns_none_when_unconfigured(channel_adapter):
    """Adapter constructed with no token/config: send must be a silent no-op."""
    name, adapter, *_ = channel_adapter
    result = adapter.send("some-channel-id", "test body")
    assert result is None, f"{name}: send must return None"


def test_send_with_attachments_does_not_raise(channel_adapter):
    """Optional attachments param: must accept it without raising."""
    name, adapter, *_ = channel_adapter
    result = adapter.send("some-channel-id", "test body", attachments=[{"url": "https://x"}])
    assert result is None, f"{name}: send(..., attachments=[...]) must return None"


def test_approve_button_returns_none(channel_adapter):
    name, adapter, *_ = channel_adapter
    result = adapter.approve_button("T-conf-1", "merge")
    assert result is None, f"{name}: approve_button must return None"


# ---------------------------------------------------------------------------
# Error-path tests — adapter must swallow transport failures
# ---------------------------------------------------------------------------
# These cover the case where the adapter IS configured (has a token/config)
# but the underlying transport (network, IMAP, SMTP) raises. The adapter must
# still return None / no-op.

def test_send_swallows_transport_failure_telegram(monkeypatch):
    """If urllib.request.urlopen raises mid-send, TelegramChannelAdapter must
    return None without propagating the exception."""
    def _raise(*a, **kw):
        raise OSError("simulated network failure")
    monkeypatch.setattr(
        "flyn_orchestrator.adapters.channels.telegram.urllib.request.urlopen",
        _raise,
    )
    adapter = TelegramChannelAdapter(bot_token="stub-token")  # configured
    result = adapter.send("12345", "body that would normally go out")
    assert result is None


def test_send_swallows_transport_failure_email(monkeypatch):
    """If the injected smtp_sender raises, EmailChannelAdapter must return None."""
    def _raise(**kw):
        raise OSError("simulated smtp failure")
    adapter = EmailChannelAdapter(
        config={"smtp_host": "smtp.example.com", "smtp_port": 587,
                "imap_host": "imap.example.com", "imap_port": 993,
                "username": "x@example.com", "password": "stub"},
        smtp_sender=_raise,
    )
    result = adapter.send("recipient@example.com", "body that would normally go out")
    assert result is None
