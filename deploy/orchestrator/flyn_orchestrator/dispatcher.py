from __future__ import annotations
from typing import Optional

from .backends import BackendRegistry, WorkerBackend, default_registry
from .backends.base import WorkerResult
from .types import WorkerSpec


class WorkerDispatcher:
    def __init__(self, registry: Optional[BackendRegistry] = None) -> None:
        self._registry = registry or default_registry()

    def register_backend(self, name: str, b: WorkerBackend) -> None:
        self._registry.register(name, b)

    def dispatch(self, spec: WorkerSpec, prompt: str) -> WorkerResult:
        backend = self._registry.get(spec.backend)
        return backend.run(spec, prompt)
