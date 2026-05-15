from __future__ import annotations

from typing import Any

import pytest

from flyn_memory_router.adapters import AdapterRegistry
from flyn_memory_router.adapters.base import MemoryAdapter, WriteResult
from flyn_memory_router.types import InboundEvent, Tier


class _StubAdapter:
    name = "stub"

    def __init__(self) -> None:
        self.writes: list[InboundEvent] = []

    def write(self, event: InboundEvent) -> WriteResult:
        self.writes.append(event)
        return WriteResult(target=self.name, ok=True, detail="stubbed")


def test_register_and_get():
    reg = AdapterRegistry()
    a = _StubAdapter()
    reg.register(Tier.WARM, a)
    assert reg.for_tier(Tier.WARM) == [a]


def test_multiple_per_tier():
    reg = AdapterRegistry()
    a, b = _StubAdapter(), _StubAdapter()
    reg.register(Tier.WARM, a)
    reg.register(Tier.WARM, b)
    assert reg.for_tier(Tier.WARM) == [a, b]


def test_empty_tier_returns_empty():
    reg = AdapterRegistry()
    assert reg.for_tier(Tier.COLD) == []


from flyn_memory_router.adapters.cold import ColdCapturesIndexAdapter


def test_cold_adapter_appends_jsonl(tmp_path):
    idx = tmp_path / "captures_index.jsonl"
    a = ColdCapturesIndexAdapter(index_path=idx)
    e = InboundEvent(source="orchestrator", event_type="stream_json_delta",
                     subject="T-0042/w-001", body="raw delta line",
                     dedup_key="orch-T-0042-w-001-seq-7")
    res = a.write(e)
    assert res.ok is True
    lines = idx.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "T-0042/w-001" in lines[0]


def test_cold_adapter_multiple_writes_append(tmp_path):
    idx = tmp_path / "captures_index.jsonl"
    a = ColdCapturesIndexAdapter(index_path=idx)
    for i in range(3):
        a.write(InboundEvent(source="orchestrator", event_type="capture_chunk",
                             subject=f"T-0001/w-001/seq-{i}", body=f"chunk-{i}",
                             dedup_key=f"k-{i}"))
    assert len(idx.read_text().strip().splitlines()) == 3


from flyn_memory_router.adapters.lesson import LessonKnowledgeAdapter


def test_lesson_adapter_writes_new_file(tmp_path):
    a = LessonKnowledgeAdapter(knowledge_dir=tmp_path)
    e = InboundEvent(
        source="orchestrator", event_type="lesson_learned",
        subject="oauth-refresh-flaky-on-headless",
        body="When `claude -p` ran for >2h, OAuth refresh failed silently. Mitigation: set ANTHROPIC_API_KEY as fallback.",
        dedup_key="lesson-oauth-refresh-2026-05-15",
    )
    res = a.write(e)
    assert res.ok is True
    files = list(tmp_path.glob("*-oauth-refresh-flaky-on-headless.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "name: oauth-refresh-flaky-on-headless" in text
    assert "ANTHROPIC_API_KEY" in text


def test_lesson_adapter_dedups_by_subject(tmp_path):
    a = LessonKnowledgeAdapter(knowledge_dir=tmp_path)
    e1 = InboundEvent(source="x", event_type="lesson_learned",
                      subject="some-lesson", body="first version",
                      dedup_key="k1")
    e2 = InboundEvent(source="x", event_type="lesson_learned",
                      subject="some-lesson", body="updated version",
                      dedup_key="k2")
    a.write(e1)
    a.write(e2)
    files = list(tmp_path.glob("*-some-lesson.md"))
    assert len(files) == 1
    assert "updated version" in files[0].read_text()


from datetime import datetime, timezone
from flyn_memory_router.adapters.cool import CoolDailyRollupAdapter


def test_cool_appends_to_today_file(tmp_path):
    a = CoolDailyRollupAdapter(memory_dir=tmp_path, today=lambda: datetime(2026, 5, 15, tzinfo=timezone.utc))
    e = InboundEvent(source="orchestrator", event_type="worker_dispatched",
                     subject="T-0042/w-001", body="builder dispatched on src/api/sponsors.*",
                     dedup_key="orch-w-001-dispatch")
    a.write(e)
    f = tmp_path / "orchestrator" / "2026-05-15-cool-events.jsonl"
    assert f.exists()
    assert "builder dispatched" in f.read_text()


def test_cool_separates_days(tmp_path):
    day1 = datetime(2026, 5, 15, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 16, tzinfo=timezone.utc)
    a1 = CoolDailyRollupAdapter(memory_dir=tmp_path, today=lambda: day1)
    a2 = CoolDailyRollupAdapter(memory_dir=tmp_path, today=lambda: day2)
    e = lambda i: InboundEvent(source="orchestrator", event_type="worker_dispatched",
                                subject=f"T-{i:04d}", body=f"e-{i}", dedup_key=f"k-{i}")
    a1.write(e(1))
    a2.write(e(2))
    assert (tmp_path / "orchestrator" / "2026-05-15-cool-events.jsonl").exists()
    assert (tmp_path / "orchestrator" / "2026-05-16-cool-events.jsonl").exists()


from unittest.mock import MagicMock, patch
from flyn_memory_router.adapters.warm import WarmGraphitiAdapter, WarmWorkspaceFileAdapter


def test_warm_graphiti_posts_episode():
    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 200
    fake_client.post.return_value.json.return_value = {"uuid": "abc-123"}
    a = WarmGraphitiAdapter(graphiti_url="http://localhost:8100", http=fake_client)
    e = InboundEvent(source="orchestrator", event_type="task_completed",
                     subject="T-0042", body="T-0042 completed: PR #48 merged, deploy fired.",
                     dedup_key="orch-T-0042-completed")
    res = a.write(e)
    assert res.ok is True
    fake_client.post.assert_called_once()
    call_args = fake_client.post.call_args
    assert call_args[0][0].endswith("/api/episode")
    body = call_args[1]["json"]
    assert body["body"] == e.body
    assert body["name"].startswith("T-0042")


def test_warm_graphiti_returns_not_ok_on_500():
    fake_client = MagicMock()
    fake_client.post.return_value.status_code = 500
    fake_client.post.return_value.text = "internal error"
    a = WarmGraphitiAdapter(graphiti_url="http://localhost:8100", http=fake_client)
    e = InboundEvent(source="x", event_type="x", subject="s",
                     body="b" * 20, dedup_key="x")
    res = a.write(e)
    assert res.ok is False
    assert "500" in res.detail


def test_warm_workspace_file_writes_dated_markdown(tmp_path):
    a = WarmWorkspaceFileAdapter(memory_dir=tmp_path)
    e = InboundEvent(source="orchestrator", event_type="task_completed",
                     subject="T-0042", body="merged + deployed",
                     dedup_key="orch-T-0042-completed")
    a.write(e)
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "merged + deployed" in text
    assert "T-0042" in text


from datetime import timedelta
from flyn_memory_router.adapters.hot import HotMemoryMdAdapter, PinRecord


def _hot(tmp_path, **kw):
    md = tmp_path / "MEMORY.md"
    md.write_text("# MEMORY\n\n## Active context\n\n## Active pins\n\n")
    return HotMemoryMdAdapter(memory_md=md, **kw)


def test_hot_appends_pin_under_active_pins(tmp_path):
    a = _hot(tmp_path, now=lambda: datetime(2026, 5, 15, 9, tzinfo=timezone.utc))
    e = InboundEvent(source="orchestrator", event_type="approval_granted",
                     subject="T-0042", body="Beth approved plan for T-0042 at 09:00 UTC",
                     dedup_key="orch-T-0042-plan-approved")
    a.write(e)
    text = (tmp_path / "MEMORY.md").read_text()
    assert "T-0042" in text
    assert "Beth approved" in text
    assert "Active pins" in text


def test_hot_decay_removes_expired_pins(tmp_path):
    now = datetime(2026, 5, 15, 9, tzinfo=timezone.utc)
    a = _hot(tmp_path, now=lambda: now,
             completed_ttl=timedelta(hours=24),
             active_ttl=timedelta(hours=72))
    old = now - timedelta(hours=80)  # past both TTLs
    a._store.upsert(PinRecord(subject="OLD-1", body="stale pin", pinned_at=old,
                              permanent=False, task_state="active", last_updated=old))
    a.decay()
    text = (tmp_path / "MEMORY.md").read_text()
    assert "OLD-1" not in text


def test_hot_permanent_survives_decay(tmp_path):
    now = datetime(2026, 5, 15, 9, tzinfo=timezone.utc)
    a = _hot(tmp_path, now=lambda: now)
    old = now - timedelta(hours=240)
    a._store.upsert(PinRecord(subject="PERM-1", body="forever",
                              pinned_at=old, permanent=True, task_state="active"))
    a.decay()
    assert "PERM-1" in (tmp_path / "MEMORY.md").read_text()


def test_hot_decay_uses_last_updated_not_pinned_at(tmp_path):
    """REGRESSION: a pin re-touched recently should NOT decay even if originally pinned long ago."""
    now = datetime(2026, 5, 15, 9, tzinfo=timezone.utc)
    a = _hot(tmp_path, now=lambda: now,
             completed_ttl=timedelta(hours=24),
             active_ttl=timedelta(hours=72))
    # Insert a stale pin (created 80h ago, past active TTL; last_updated also old)
    old = now - timedelta(hours=80)
    a._store.upsert(PinRecord(subject="REFRESH-1", body="stale-then-refreshed",
                              pinned_at=old, permanent=False, task_state="active",
                              last_updated=old))
    # Re-upsert it (touching last_updated to now) but keep original pinned_at
    a._store.upsert(PinRecord(subject="REFRESH-1", body="just refreshed",
                              pinned_at=old, permanent=False, task_state="active",
                              last_updated=now))
    a.decay()
    assert "REFRESH-1" in (tmp_path / "MEMORY.md").read_text()


def test_hot_write_preserves_existing_permanent(tmp_path):
    """REGRESSION: a permanent pin must not be silently demoted by a later non-permanent write."""
    a = _hot(tmp_path, now=lambda: datetime(2026, 5, 15, 9, tzinfo=timezone.utc))
    # Owner marks it permanent
    a.pin_permanent("T-42", "important fact")
    # Orchestrator emits an update event without setting permanent
    a.write(InboundEvent(source="orchestrator", event_type="task_active_pin",
                         subject="T-42", body="status updated",
                         dedup_key="orch-T-42-update"))
    # The pin must still be permanent (won't be touched by decay)
    pins = a._store.list_all()
    perm_pin = next(p for p in pins if p.subject == "T-42")
    assert perm_pin.permanent is True
