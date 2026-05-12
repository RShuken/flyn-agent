"""Pydantic request/response models for the OL PM wiki API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ----- Question -----

class Question(BaseModel):
    id: str
    section: str
    section_title: str
    text: str
    ask: str | None = None
    bucket: str
    source: str | None = None
    owner: str
    status: str = "open"           # open | pending-answer | answered | deferred
    depends_on: list[str] = Field(default_factory=list)
    target_sprint: int | None = None
    answered_at: datetime | None = None
    answered_by: str | None = None
    answer_text: str | None = None
    source_doc: str | None = None
    updated_at: datetime


class AnswerQuestion(BaseModel):
    answer_text: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)


class ReassignQuestion(BaseModel):
    owner: str = Field(min_length=1)
    reason: str | None = None


# ----- Decision -----

class Decision(BaseModel):
    id: int
    decided_at: datetime
    decided_by: str
    summary: str
    body_md: str
    question_ids: list[str] = Field(default_factory=list)
    source_meeting: str | None = None


class NewDecision(BaseModel):
    decided_by: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=300)
    body_md: str = Field(min_length=1)
    question_ids: list[str] = Field(default_factory=list)
    source_meeting: str | None = None


# ----- Audit -----

class AuditEntry(BaseModel):
    id: int
    ts: datetime
    actor: str
    action: str
    payload: dict[str, Any]


# ----- Aggregates -----

class Stats(BaseModel):
    questions_total: int
    by_status: dict[str, int]
    by_owner: dict[str, int]
    by_sprint: dict[str, int]   # keys: "1", "2", "3", "none"
    by_bucket: dict[str, int]
    decisions_total: int
    last_audit_at: datetime | None


class Health(BaseModel):
    status: str = "ok"
    db: str
    questions_count: int


# ----- Webhook -----

class Webhook(BaseModel):
    id: int
    target_url: str
    event_types: list[str] = Field(default_factory=list)
    label: str | None = None
    active: bool = True
    created_at: datetime
    last_fired_at: datetime | None = None
    last_status: int | None = None


class NewWebhook(BaseModel):
    target_url: str = Field(min_length=8)
    event_types: list[str] = Field(default_factory=lambda: ["*"])
    secret: str | None = None
    label: str | None = None
