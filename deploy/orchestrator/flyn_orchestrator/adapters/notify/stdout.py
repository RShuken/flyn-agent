"""Trivial debug-only notify adapter."""
from __future__ import annotations


class StdoutNotifyAdapter:
    name = "stdout"

    def send(self, event: str, audience: str) -> None:
        print(f"[NOTIFY {audience}] {event}")
