"""Adapter registries."""
from __future__ import annotations
from typing import Optional
from .base import ChannelAdapter, NotifyAdapter, PMAdapter


class _NamedRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, object] = {}

    def register(self, adapter) -> None:
        self._by_name[adapter.name] = adapter

    def get(self, name: str):
        if name not in self._by_name:
            raise KeyError(f"adapter not registered: {name!r}")
        return self._by_name[name]

    def all(self) -> list:
        return list(self._by_name.values())


class ChannelRegistry(_NamedRegistry): pass
class NotifyRegistry(_NamedRegistry): pass
class PMRegistry(_NamedRegistry): pass


__all__ = ["ChannelAdapter", "NotifyAdapter", "PMAdapter",
           "ChannelRegistry", "NotifyRegistry", "PMRegistry"]
