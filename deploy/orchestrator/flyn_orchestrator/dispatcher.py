from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from .backends import BackendRegistry, WorkerBackend, default_registry
from .backends.base import WorkerResult
from .cost import CostTracker
from .types import WorkerSpec

if TYPE_CHECKING:
    from .watchdog import Watchdog


class WorkerProducedNothing(Exception):
    """Raised when a worker exits with a capture file < 100 bytes — implies the
    process emitted no real output (e.g., bad command-line flags, missing binary,
    OAuth refresh failure)."""


_MIN_CAPTURE_BYTES = 100


class WorkerDispatcher:
    def __init__(self, registry: Optional[BackendRegistry] = None) -> None:
        self._registry = registry or default_registry()

    def register_backend(self, name: str, b: WorkerBackend) -> None:
        self._registry.register(name, b)

    def dispatch(
        self,
        spec: WorkerSpec,
        prompt: str,
        *,
        watchdog: Optional["Watchdog"] = None,
    ) -> WorkerResult:
        """Dispatch spec to a backend and return the WorkerResult.

        If *watchdog* is provided it is started as a daemon thread before the
        backend call and stopped (joined) in the finally block.  This keeps
        the integration opt-in so existing callers that pass no watchdog are
        completely unaffected.
        """
        backend = self._registry.get(spec.backend)
        tracker = CostTracker(budget_usd=spec.budget_usd)

        if watchdog is not None:
            watchdog.start()
        try:
            result = backend.run(spec, prompt, cost_tracker=tracker)
        finally:
            if watchdog is not None:
                watchdog.stop()

        try:
            size = result.capture_path.stat().st_size
        except OSError:
            size = 0
        if size < _MIN_CAPTURE_BYTES:
            raise WorkerProducedNothing(
                f"worker {spec.worker_id} produced {size}-byte capture at "
                f"{result.capture_path} — check command/auth/binary"
            )
        return result
