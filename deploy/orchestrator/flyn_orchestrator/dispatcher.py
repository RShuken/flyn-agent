from __future__ import annotations
from typing import Optional

from .backends import BackendRegistry, WorkerBackend, default_registry
from .backends.base import WorkerResult
from .cost import CostTracker
from .types import WorkerSpec


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

    def dispatch(self, spec: WorkerSpec, prompt: str) -> WorkerResult:
        backend = self._registry.get(spec.backend)
        tracker = CostTracker(budget_usd=spec.budget_usd)
        result = backend.run(spec, prompt, cost_tracker=tracker)
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
