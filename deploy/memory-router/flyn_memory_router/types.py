"""Pydantic models for the MemoryRouter ingress and internal types."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Tier(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COOL = "cool"
    COLD = "cold"
    LESSON = "lesson"


Importance = Literal["hot", "warm", "cool", "cold", "lesson"]


class InboundEvent(BaseModel):
    """One memory-ingestion event accepted at /api/memory/ingest."""

    source: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="origin pipeline: orchestrator|telegram|email|fathom|krisp|wiki|manual|...",
    )
    event_type: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="semantic event type: task_created|review_complete|meeting_summary|...",
    )
    subject: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="short identifier or entity the event is about",
    )
    body: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="prose canonical fact; goes into Graphiti episodes verbatim for warm+",
    )
    importance: Optional[Importance] = Field(
        None,
        description="hot|warm|cool|cold|lesson; if absent, router classifies",
    )
    raw_payload: Optional[dict[str, Any]] = Field(
        None, description="optional structured data, not sent to Graphiti"
    )
    valid_at: Optional[datetime] = Field(
        None, description="when the fact became true; defaults to ingest time"
    )
    dedup_key: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="idempotency key; (source, dedup_key) is the actual key",
    )
    sender_role: Optional[Literal["owner", "teammate", "other"]] = Field(
        None,
        description="caller role tier; required for /api/memory/pin permanent flag",
    )

    @field_validator("body")
    @classmethod
    def _body_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body must not be whitespace-only")
        return v


class EventResult(BaseModel):
    """Returned from POST /api/memory/ingest."""

    accepted: bool
    deduped: bool
    importance: Importance
    tiers_written: list[Tier]
    notes: list[str] = Field(default_factory=list)
