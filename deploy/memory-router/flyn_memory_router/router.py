"""Core router: classify -> dedup -> fan-out to registered adapters."""
from __future__ import annotations

from .adapters import AdapterRegistry
from .classifier import classify
from .dedup import DedupStore
from .types import EventResult, InboundEvent, Tier


class Router:
    def __init__(self, registry: AdapterRegistry, dedup: DedupStore) -> None:
        self._registry = registry
        self._dedup = dedup

    def ingest(self, event: InboundEvent) -> EventResult:
        importance = classify(event)
        tier = Tier(importance)
        if self._dedup.seen(event.source, event.dedup_key):
            return EventResult(
                accepted=True, deduped=True, importance=importance,
                tiers_written=[], notes=["skipped: dedup hit"],
            )
        self._dedup.record(event.source, event.dedup_key)
        notes: list[str] = []
        adapters = self._registry.for_tier(tier)
        if not adapters:
            notes.append(f"no adapter registered for tier={tier.value}")
        for a in adapters:
            try:
                res = a.write(event)
            except Exception as ex:  # noqa: BLE001 — adapter errors must never crash router
                notes.append(f"{a.name}: EXC {type(ex).__name__}: {ex!s}"[:200])
                continue
            if not res.ok:
                notes.append(f"{res.target}: not ok: {res.detail}"[:200])
        return EventResult(
            accepted=True, deduped=False, importance=importance,
            tiers_written=[tier] if adapters else [], notes=notes,
        )
