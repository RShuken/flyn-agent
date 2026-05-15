"""Importance classifier. Rule-based first; an LLM fallback can replace `_default()` later.

Adding a new event_type:
    - if it's worth pinning to MEMORY.md: add to _HOT
    - if it's a meaningful decision/deliverable/approval: add to _WARM (default)
    - if it's a minor activity log: add to _COOL
    - if it's raw telemetry: add to _COLD
    - if it's a distilled long-form lesson: add to _LESSON
"""
from __future__ import annotations

from .types import Importance, InboundEvent

_HOT = {
    "approval_granted",
    "approval_revoked",
    "task_active_pin",
}

_WARM = {
    "task_created",
    "task_decomposed",
    "task_completed",
    "task_failed",
    "task_cancelled",
    "review_complete",
    "review_changes_requested",
    "deliverable_ready",
    "merge_completed",
    "deploy_fired",
    "meeting_summary",
    "decision_recorded",
    "config_changed",
}

_COOL = {
    "worker_dispatched",
    "worker_exit",
    "worker_nudged",
    "watchdog_triage",
    "cost_event",
    "mirror_synced",
}

_COLD = {
    "stream_json_delta",
    "capture_chunk",
    "heartbeat_tick",
}

_LESSON = {
    "lesson_learned",
}


def classify(event: InboundEvent) -> Importance:
    if event.importance is not None:
        return event.importance
    et = event.event_type
    if et in _HOT:
        return "hot"
    if et in _COOL:
        return "cool"
    if et in _COLD:
        return "cold"
    if et in _LESSON:
        return "lesson"
    if et in _WARM:
        return "warm"
    return _default()


def _default() -> Importance:
    """Unknown event types default to warm. Never silently lose a fact.

    Future work: cheap-LLM classifier via gemma4:e4b. For Phase 0, the rule set
    is intentionally exhaustive enough that this fallback rarely fires.
    """
    return "warm"
