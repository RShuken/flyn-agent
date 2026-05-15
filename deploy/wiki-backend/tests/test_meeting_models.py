"""Pydantic model validation for meeting payloads."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import MeetingRow, KrispWebhookAck  # noqa: E402


def test_meeting_row_minimal():
    m = MeetingRow(meeting_id="m-1")
    assert m.meeting_id == "m-1"
    assert m.status == "pending"
    assert m.attendees == []


def test_meeting_row_full():
    m = MeetingRow(
        meeting_id="m-1",
        title="Sprint sync",
        attendees=[{"name": "Beth", "email": "beth@example.com"}],
        transcript_text="hello",
        status="routed",
    )
    assert m.attendees[0]["email"] == "beth@example.com"


def test_krisp_ack_shape():
    ack = KrispWebhookAck(received=True, event_id="ev-1")
    assert ack.received is True
    assert ack.duplicate is False
