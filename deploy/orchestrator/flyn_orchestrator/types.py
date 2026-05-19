# deploy/orchestrator/flyn_orchestrator/types.py
"""Pydantic models for orchestrator REST + internal handoffs."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TaskState(str, Enum):
    INBOUND = "inbound"
    TRIAGING = "triaging"
    HUMAN_REVIEW_QUEUE = "human_review_queue"
    ROUTED = "routed"
    DECOMPOSED = "decomposed"
    PLAN_PENDING = "plan_pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    REVIEWED = "reviewed"
    DELIVERABLE_READY = "deliverable_ready"
    FINAL_APPROVAL_PENDING = "final_approval_pending"
    AWAITING_OWNER_APPROVAL = "awaiting_owner_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CHANGES_REQUESTED = "changes_requested"
    SECURITY_REVIEW = "security_review"
    COST_PAUSED = "cost_paused"


class WorkerRole(str, Enum):
    PM = "pm"
    ARCHITECT = "architect"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    SANITIZER = "sanitizer"
    RESEARCHER = "researcher"
    CRITIC = "critic"
    SYNTHESIZER = "synthesizer"
    WRITER = "writer"
    EDITOR = "editor"
    FACT_CHECKER = "fact_checker"
    EXECUTOR = "executor"
    VALIDATOR = "validator"


SenderRole = Literal["owner", "teammate", "other"]


class InboundTaskRequest(BaseModel):
    """Posted to /api/tasks/inbound from a channel adapter or manual curl."""

    channel: str = Field(..., min_length=1, max_length=64)
    sender_identifier: str = Field(..., min_length=1, max_length=128)
    sender_role: SenderRole
    intent: str = Field(..., min_length=1, max_length=4000)
    external_message_id: str = Field(..., min_length=1, max_length=256)
    workflow_override: Optional[str] = Field(None, description="explicit workflow name; else router routes by intent")
    raw_payload: Optional[dict[str, Any]] = None


class TaskRecord(BaseModel):
    task_id: str
    workflow: str
    state: TaskState
    sender_role: SenderRole
    sender_identifier: str
    intent: str
    created_at: Optional[datetime] = None
    budget_usd: float = 5.0
    raw_payload: Optional[dict[str, Any]] = None


class WorkerSpec(BaseModel):
    task_id: str
    worker_id: str
    role: WorkerRole
    # NOTE: field default is NOT a routing decision — callers must always set this
    # explicitly. "noop" is chosen so that specs constructed without an explicit backend
    # fail safely rather than consuming OAuth tokens.
    backend: str = Field(default="noop", description="WorkerBackend name in backends registry")
    prompt_template: str
    worktree_path: str
    max_turns: int = 10
    budget_usd: float = 5.0
    allowed_tools: Optional[list[str]] = None
    readonly: bool = False


class WorkerHandle(BaseModel):
    worker_id: str
    pid: Optional[int] = None
    capture_path: Optional[str] = None
    started_at: Optional[datetime] = None
    role: WorkerRole


class ReviewFinding(BaseModel):
    severity: Literal["info", "minor", "important", "critical"]
    area: str = Field(..., description="correctness | security | performance | architecture | ux | style")
    note: str


class ReviewFindings(BaseModel):
    worker_id: str
    passed: bool
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: str = ""


class ApprovalGate(BaseModel):
    name: str
    who: SenderRole
    when: Literal["always", "condition"] = "always"
    condition: Optional[str] = None


class ApprovalDecision(BaseModel):
    task_id: str
    gate: str
    approver: str
    approved: bool
    reason: Optional[str] = None
    decided_at: Optional[datetime] = None


class EventResultLite(BaseModel):
    """Subset of router EventResult fields the orchestrator depends on."""

    accepted: bool
    deduped: bool
