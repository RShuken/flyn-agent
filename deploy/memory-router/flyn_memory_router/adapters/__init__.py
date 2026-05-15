"""AdapterRegistry: maps Tier -> [MemoryAdapter, ...]. Multiple adapters per tier are fine."""
from __future__ import annotations

from collections import defaultdict

from ..types import Tier
from .base import MemoryAdapter, WriteResult


class AdapterRegistry:
    def __init__(self) -> None:
        self._by_tier: dict[Tier, list[MemoryAdapter]] = defaultdict(list)

    def register(self, tier: Tier, adapter: MemoryAdapter) -> None:
        self._by_tier[tier].append(adapter)

    def for_tier(self, tier: Tier) -> list[MemoryAdapter]:
        return list(self._by_tier.get(tier, []))


__all__ = ["AdapterRegistry", "MemoryAdapter", "WriteResult"]
