"""MemoryAdapter Protocol — one implementation per tier-target."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..types import InboundEvent


@dataclass(frozen=True)
class WriteResult:
    target: str          # adapter name, e.g. "warm.graphiti" or "hot.memory_md"
    ok: bool
    detail: str = ""     # short status string (redacted by caller before logging)


@runtime_checkable
class MemoryAdapter(Protocol):
    """Implement `write(event)`. Adapter is registered against one or more tiers."""

    name: str

    def write(self, event: InboundEvent) -> WriteResult:
        ...
