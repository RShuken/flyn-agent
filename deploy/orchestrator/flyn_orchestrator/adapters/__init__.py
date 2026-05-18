"""Adapter registries.

Each registry holds named adapters of a particular type (ChannelRegistry for
ChannelAdapter, NotifyRegistry for NotifyAdapter, PMRegistry for PMAdapter).

The base registry supports an optional `memory_emitter` for centralized
observability wiring. When set at construction time (or via the
`attach_memory_emitter` method later), every registered adapter that exposes
a `_memory_emitter` attribute and has it set to None gets the registry's
default emitter wired in. Adapters constructed with an explicit
`memory_emitter` kwarg are NOT overwritten — explicit configuration wins.

See `audit/_baseline.md §Δ.adapter-observability` and KNOWLEDGE/20 for the
swallowed-error observability pattern this enables.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from .base import ChannelAdapter, NotifyAdapter, PMAdapter

if TYPE_CHECKING:
    from ..memory import MemoryEmitter


class _NamedRegistry:
    """Base registry: name → adapter, with optional default memory_emitter
    auto-wired into every adapter that has the slot."""

    def __init__(self, memory_emitter: Optional["MemoryEmitter"] = None) -> None:
        self._by_name: dict[str, object] = {}
        self._default_memory_emitter = memory_emitter

    def register(self, adapter) -> None:
        """Register *adapter* by its `.name` attribute. If the registry has a
        default memory_emitter AND the adapter has `_memory_emitter` slot set
        to None, the registry's emitter is auto-wired. Adapters with an
        explicit memory_emitter pass through unchanged."""
        self._maybe_wire(adapter)
        self._by_name[adapter.name] = adapter

    def get(self, name: str):
        if name not in self._by_name:
            raise KeyError(f"adapter not registered: {name!r}")
        return self._by_name[name]

    def all(self) -> list:
        return list(self._by_name.values())

    def attach_memory_emitter(self, memory_emitter: "MemoryEmitter") -> None:
        """Set the registry's default emitter and retro-wire it into every
        already-registered adapter that has an unset `_memory_emitter` slot.
        Useful when memory_emitter is constructed AFTER the adapters."""
        self._default_memory_emitter = memory_emitter
        for adapter in self._by_name.values():
            self._maybe_wire(adapter)

    def _maybe_wire(self, adapter) -> None:
        """Set `adapter._memory_emitter` to the registry's default IFF the slot
        exists AND is currently None. Never overwrites explicit configuration."""
        if self._default_memory_emitter is None:
            return
        if not hasattr(adapter, "_memory_emitter"):
            return
        if getattr(adapter, "_memory_emitter", None) is None:
            adapter._memory_emitter = self._default_memory_emitter


class ChannelRegistry(_NamedRegistry): pass
class NotifyRegistry(_NamedRegistry): pass
class PMRegistry(_NamedRegistry): pass


__all__ = ["ChannelAdapter", "NotifyAdapter", "PMAdapter",
           "ChannelRegistry", "NotifyRegistry", "PMRegistry"]
