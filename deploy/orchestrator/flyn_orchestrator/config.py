from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    workspace: Path
    port: int
    router_url: str
    default_backend: str
    concurrent_tasks_max: int
    concurrent_workers_max: int

    @property
    def db_path(self) -> Path: return self.home / "data" / "state.db"
    @property
    def workspaces_dir(self) -> Path: return self.home / "workspaces"
    @property
    def captures_dir(self) -> Path: return self.home / "captures"
    @property
    def coordination_dir(self) -> Path: return self.home / "coordination"

    @classmethod
    def from_env(cls) -> "Config":
        home = Path(os.environ.get("FLYN_ORCHESTRATOR_HOME",
                                    str(Path.home() / ".flyn" / "orchestrator")))
        workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                         str(Path.home() / ".openclaw" / "workspace")))
        return cls(
            home=home,
            workspace=workspace,
            port=int(os.environ.get("FLYN_ORCHESTRATOR_PORT", "8300")),
            router_url=os.environ.get("FLYN_MEMORY_ROUTER_URL", "http://localhost:8400"),
            default_backend=os.environ.get("FLYN_DEFAULT_BACKEND", "claude-p"),
            concurrent_tasks_max=int(os.environ.get("FLYN_CONCURRENT_TASKS_MAX", "4")),
            concurrent_workers_max=int(os.environ.get("FLYN_CONCURRENT_WORKERS_MAX", "6")),
        )
