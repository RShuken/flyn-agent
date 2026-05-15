"""Runtime configuration. All paths and ports come from env. No hardcoded paths in modules."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path                         # ~/.flyn/memory-router by default
    workspace: Path                    # ~/.openclaw/workspace by default
    port: int
    passthrough_mode: bool
    graphiti_url: str
    knowledge_dir: Path                # flyn-agent/KNOWLEDGE by default

    @property
    def db_path(self) -> Path:
        return self.home / "data" / "router.db"

    @property
    def queue_dir(self) -> Path:
        return self.home / "queue"

    @property
    def memory_md(self) -> Path:
        return self.workspace / "MEMORY.md"

    @property
    def workspace_memory_dir(self) -> Path:
        return self.workspace / "memory"

    @classmethod
    def from_env(cls) -> "Config":
        home = Path(os.environ.get("FLYN_MEMORY_ROUTER_HOME",
                                    str(Path.home() / ".flyn" / "memory-router")))
        workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                         str(Path.home() / ".openclaw" / "workspace")))
        port = int(os.environ.get("FLYN_MEMORY_ROUTER_PORT", "8400"))
        passthrough = os.environ.get("FLYN_MEMORY_ROUTER_PASSTHROUGH", "false").lower() == "true"
        graphiti_url = os.environ.get("FLYN_GRAPHITI_URL", "http://localhost:8100")
        knowledge_dir = Path(os.environ.get("FLYN_KNOWLEDGE_DIR",
                                             str(Path.home() / "AI" / "openclaw" / "flyn-agent" / "KNOWLEDGE")))
        return cls(home=home, workspace=workspace, port=port,
                   passthrough_mode=passthrough, graphiti_url=graphiti_url,
                   knowledge_dir=knowledge_dir)
