# deploy/orchestrator/flyn_orchestrator/phase_services.py
"""Shared services bundle passed to phase-runner modules.

Frozen dataclass: phase runners read but never mutate. Eliminates threading
8+ individual arguments through every phase function signature, and avoids
coupling phase modules to the TaskRouter class itself.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters import ChannelRegistry
    from .backends import BackendRegistry
    from .config import Config
    from .memory import MemoryEmitter
    from .state import StateStore
    from .types import ReviewFindings, TaskState


@dataclass(frozen=True)
class PhaseServices:
    store: "StateStore"
    memory: "MemoryEmitter"
    channels: Optional["ChannelRegistry"]
    reviewer_invoker: Callable[..., "ReviewFindings"]
    transition: Callable[..., None]
    safe_transition: Callable[..., None]
    notify: Callable[..., None]
    backend_registry: "BackendRegistry"
    scratch_root: Path
    repo_path_for_workflow: Callable[[str], Path]
    workflows_dir: Path
    config: Optional["Config"] = field(default=None)
