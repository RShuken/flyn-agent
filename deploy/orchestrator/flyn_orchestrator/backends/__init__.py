# deploy/orchestrator/flyn_orchestrator/backends/__init__.py
from __future__ import annotations
from .base import WorkerBackend, WorkerResult
from .claude_p import ClaudePBackend
from .codex_exec import CodexExecBackend


class BackendRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, WorkerBackend] = {}

    def register(self, name: str, b: WorkerBackend) -> None:
        self._by_name[name] = b

    def get(self, name: str) -> WorkerBackend:
        if name not in self._by_name:
            raise KeyError(f"no backend registered: {name!r}")
        return self._by_name[name]


_DEFAULT_REGISTRY = BackendRegistry()
_DEFAULT_REGISTRY.register("claude-p", ClaudePBackend())
_DEFAULT_REGISTRY.register("codex-exec", CodexExecBackend())


def default_registry() -> BackendRegistry:
    return _DEFAULT_REGISTRY


def get_backend(name: str) -> WorkerBackend:
    return _DEFAULT_REGISTRY.get(name)


__all__ = [
    "BackendRegistry",
    "WorkerBackend",
    "WorkerResult",
    "default_registry",
    "get_backend",
    "ClaudePBackend",
    "CodexExecBackend",
]
