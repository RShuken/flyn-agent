"""MemoryAdapter Protocol — one implementation per tier-target."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..types import InboundEvent, Hit


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


@runtime_checkable
class ReadAdapter(Protocol):
    """Implement `query(q, top_k)`. Returned hits use adapter-native scoring;
    cross-source ranking happens in query.py via RRF."""

    name: str
    read_timeout: float
    default_included: bool

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        ...
