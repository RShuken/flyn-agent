"""Backpressure policy for conv-tier 2.0 ingest.

When total queue depth exceeds HIGH_WATER, ingest applies a configured
drop policy and emits an overload signal. The system continues operating
but signals overload to the controller (openclaw plugin) so it can
throttle upstream.

Drop policies:
- reject_new: ingest returns 503; the message is dropped (caller retries).
- drop_oldest: oldest queued message is dropped; new one accepted.
- drop_newest: new message dropped, queue unchanged (mostly equivalent
  to reject_new but doesn't emit 503).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from .schema import open_db
from .work_queue import WorkQueue

logger = logging.getLogger(__name__)

DEFAULT_HIGH_WATER = int(os.environ.get("FLYN_CONV_HIGH_WATER", "1000"))
DEFAULT_DROP_POLICY = os.environ.get("FLYN_CONV_DROP_POLICY", "reject_new")

DropPolicy = Literal["reject_new", "drop_oldest", "drop_newest"]


class OverloadError(Exception):
    """Raised when ingest is refused under reject_new policy."""


@dataclass
class BackpressureState:
    """Tracks overload state for /health reporting."""

    high_water: int = DEFAULT_HIGH_WATER
    policy: DropPolicy = DEFAULT_DROP_POLICY  # type: ignore[assignment]
    last_drop_at: str | None = None
    total_drops: int = 0
    active: bool = False


async def check_and_apply(
    queue: WorkQueue,
    state: BackpressureState,
) -> None:
    """Check queue depth before accepting a new message.

    Raises OverloadError if reject_new policy says no. Otherwise applies
    drop_oldest/drop_newest as configured. Updates state.last_drop_at +
    state.total_drops + state.active. Returns silently if not overloaded.
    """
    depth = await queue.total_depth()
    if depth < state.high_water:
        state.active = False
        return

    # We are overloaded. Apply policy.
    state.active = True
    state.last_drop_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.total_drops += 1

    logger.warning(
        "backpressure.overload depth=%d high_water=%d policy=%s",
        depth, state.high_water, state.policy,
    )

    if state.policy == "reject_new":
        raise OverloadError(
            f"conv2 ingest at capacity (depth={depth}, high_water={state.high_water})"
        )
    elif state.policy == "drop_oldest":
        await _drop_oldest(queue)
    elif state.policy == "drop_newest":
        # New message dropped silently; ingest returns success but no row written
        # (caller handles this via a sentinel result)
        raise OverloadError("drop_newest: new message discarded")
    else:
        raise OverloadError(f"unknown drop policy: {state.policy}")


async def _drop_oldest(queue: WorkQueue) -> None:
    """Remove the oldest work_queue row from any stage."""
    def _drop() -> None:
        with open_db(queue.db_path()) as conn:
            conn.execute(
                "DELETE FROM work_queue "
                "WHERE id = (SELECT id FROM work_queue ORDER BY enqueued_at LIMIT 1)"
            )
    await asyncio.to_thread(_drop)
