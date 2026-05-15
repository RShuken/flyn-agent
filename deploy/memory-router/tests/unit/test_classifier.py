from __future__ import annotations

import pytest

from flyn_memory_router.classifier import classify
from flyn_memory_router.types import InboundEvent


def _e(event_type: str, body: str = "x" * 20, source: str = "orchestrator") -> InboundEvent:
    return InboundEvent(
        source=source, event_type=event_type, subject="s",
        body=body, dedup_key=f"{event_type}-1",
    )


def test_explicit_importance_passthrough():
    e = InboundEvent(source="orchestrator", event_type="task_created", subject="s",
                     body="x" * 20, dedup_key="x", importance="cold")
    assert classify(e) == "cold"


def test_orchestrator_task_lifecycle_is_warm():
    assert classify(_e("task_created")) == "warm"
    assert classify(_e("task_completed")) == "warm"
    assert classify(_e("review_complete")) == "warm"


def test_approval_is_hot():
    assert classify(_e("approval_granted")) == "hot"


def test_worker_dispatch_is_cool():
    assert classify(_e("worker_dispatched")) == "cool"


def test_raw_capture_is_cold():
    assert classify(_e("stream_json_delta")) == "cold"


def test_lesson_event_is_lesson():
    assert classify(_e("lesson_learned")) == "lesson"


def test_meeting_summary_is_warm():
    assert classify(_e("meeting_summary", source="fathom")) == "warm"
    assert classify(_e("meeting_summary", source="krisp")) == "warm"


def test_unknown_event_defaults_warm():
    assert classify(_e("something_unrecognized")) == "warm"
