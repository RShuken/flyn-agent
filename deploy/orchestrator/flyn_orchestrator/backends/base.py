# deploy/orchestrator/flyn_orchestrator/backends/base.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..types import WorkerSpec


@dataclass(frozen=True)
class WorkerResult:
    worker_id: str
    exit_code: int
    capture_path: Path
    cost_usd: float
    duration_ms: int
    changed_files: list[str]
    summary: str = ""


@runtime_checkable
class WorkerBackend(Protocol):
    name: str

    def run(self, spec: WorkerSpec, prompt: str) -> WorkerResult:
        """Spawn the worker subprocess, stream output to spec's capture path,
        block until done or until max_turns / budget hit, return WorkerResult."""
        ...
