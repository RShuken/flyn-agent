from __future__ import annotations

from pathlib import Path

import pytest

from flyn_memory_router.adapters import AdapterRegistry
from flyn_memory_router.adapters.base import WriteResult
from flyn_memory_router.dedup import DedupStore
from flyn_memory_router.router import Router
from flyn_memory_router.types import InboundEvent, Tier


class _RecordingAdapter:
    def __init__(self, name: str, ok: bool = True) -> None:
        self.name = name
        self.calls: list[InboundEvent] = []
        self._ok = ok

    def write(self, event: InboundEvent) -> WriteResult:
        self.calls.append(event)
        return WriteResult(target=self.name, ok=self._ok)


@pytest.fixture
def router(tmp_path: Path) -> tuple[Router, AdapterRegistry, DedupStore]:
    reg = AdapterRegistry()
    ds = DedupStore(db_path=tmp_path / "router.db")
    return Router(registry=reg, dedup=ds), reg, ds


def test_router_dedup_skips_second_call(router):
    r, reg, _ = router
    a = _RecordingAdapter("warm.stub")
    reg.register(Tier.WARM, a)
    e = InboundEvent(source="orchestrator", event_type="task_created", subject="T-1",
                     body="b" * 20, dedup_key="k-1")
    res1 = r.ingest(e)
    res2 = r.ingest(e)
    assert res1.deduped is False
    assert res2.deduped is True
    assert len(a.calls) == 1


def test_router_warm_writes_workspace_and_graphiti(router):
    r, reg, _ = router
    wsa = _RecordingAdapter("warm.workspace_file")
    gra = _RecordingAdapter("warm.graphiti")
    reg.register(Tier.WARM, wsa)
    reg.register(Tier.WARM, gra)
    e = InboundEvent(source="orchestrator", event_type="task_completed", subject="T-2",
                     body="merged", dedup_key="k-2")
    res = r.ingest(e)
    assert res.accepted is True
    assert res.importance == "warm"
    assert Tier.WARM in res.tiers_written
    assert len(wsa.calls) == 1
    assert len(gra.calls) == 1


def test_router_failed_adapter_still_marks_accepted(router):
    r, reg, _ = router
    bad = _RecordingAdapter("warm.graphiti", ok=False)
    reg.register(Tier.WARM, bad)
    e = InboundEvent(source="orchestrator", event_type="task_created", subject="T-3",
                     body="b" * 20, dedup_key="k-3")
    res = r.ingest(e)
    assert res.accepted is True
    assert any("warm.graphiti" in n for n in res.notes)


def test_router_unknown_event_defaults_warm(router):
    r, reg, _ = router
    a = _RecordingAdapter("warm.x")
    reg.register(Tier.WARM, a)
    e = InboundEvent(source="x", event_type="something_brand_new", subject="s",
                     body="b" * 20, dedup_key="k-4")
    res = r.ingest(e)
    assert res.importance == "warm"
    assert len(a.calls) == 1
