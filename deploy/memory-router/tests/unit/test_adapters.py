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
