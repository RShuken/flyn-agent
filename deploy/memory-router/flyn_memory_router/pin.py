"""Permanent-pin operations. Owner-only enforcement happens here, not in the HTTP layer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .adapters.hot import HotMemoryMdAdapter


@dataclass(frozen=True)
class PinRequest:
    subject: str
    body: str
    sender_role: Literal["owner", "teammate", "other"]


def _require_owner(role: str) -> None:
    if role != "owner":
        raise PermissionError(f"permanent pin operations require owner role; got {role!r}")


def pin_permanent(hot: HotMemoryMdAdapter, req: PinRequest) -> None:
    _require_owner(req.sender_role)
    hot.pin_permanent(req.subject, req.body)


def unpin(hot: HotMemoryMdAdapter, subject: str, *, sender_role: str) -> bool:
    _require_owner(sender_role)
    return hot.unpin(subject)
