"""Type-validation tests for InboundEvent and Tier."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from flyn_memory_router.types import InboundEvent, Tier


def test_inbound_event_minimal():
    e = InboundEvent(
        source="orchestrator",
        event_type="task_created",
        subject="T-0042",
        body="Beth opened task T-0042 in the dev workflow.",
        dedup_key="orch-T-0042-created",
    )
    assert e.source == "orchestrator"
    assert e.importance is None  # router infers when absent


def test_inbound_event_rejects_empty_body():
    with pytest.raises(ValidationError):
        InboundEvent(
            source="orchestrator",
            event_type="task_created",
            subject="T-0042",
            body="",
            dedup_key="x",
        )


def test_inbound_event_rejects_unknown_importance():
    with pytest.raises(ValidationError):
        InboundEvent(
            source="orchestrator",
            event_type="task_created",
            subject="T-0042",
            body="anything",
            dedup_key="x",
            importance="medium",  # not in the enum
        )


def test_tier_enum_values():
    assert {t.value for t in Tier} == {"hot", "warm", "cool", "cold", "lesson"}
