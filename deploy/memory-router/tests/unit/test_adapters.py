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
