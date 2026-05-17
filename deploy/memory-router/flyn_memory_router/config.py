"""Runtime configuration. All paths and ports come from env. No hardcoded paths in modules."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    workspace: Path
    port: int
    passthrough_mode: bool
    graphiti_url: str
    knowledge_dir: Path
    reference_vault: Path
    auto_memory_dir: Path
    ol_wiki_url: str
    ol_wiki_pin: str

    @property
    def db_path(self) -> Path:
        return self.home / "data" / "router.db"

    @property
    def queue_dir(self) -> Path:
        return self.home / "queue"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def memory_md(self) -> Path:
        return self.workspace / "MEMORY.md"

    @property
    def workspace_memory_dir(self) -> Path:
        return self.workspace / "memory"

    @property
    def pin_file(self) -> Path:
        return self.workspace / "pins.json"

    @property
    def captures_index(self) -> Path:
        return self.home / "captures_index.jsonl"

    @classmethod
    def from_env(cls) -> "Config":
        home_env = Path.home() / ".flyn" / "memory-router"
        home = Path(os.environ.get("FLYN_MEMORY_ROUTER_HOME", str(home_env)))
        workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                         str(Path.home() / ".openclaw" / "workspace")))
        port = int(os.environ.get("FLYN_MEMORY_ROUTER_PORT", "8400"))
        passthrough = os.environ.get("FLYN_MEMORY_ROUTER_PASSTHROUGH", "false").lower() == "true"
        graphiti_url = os.environ.get("FLYN_GRAPHITI_URL", "http://localhost:8100")
        knowledge_dir = Path(os.environ.get("FLYN_KNOWLEDGE_DIR",
                                             str(Path.home() / "AI" / "openclaw" / "flyn-agent" / "KNOWLEDGE")))
        reference_vault = Path(os.environ.get("FLYN_REFERENCE_VAULT",
                                               str(Path.home() / "AI" / "openclaw" / "reference")))
        auto_memory_dir = Path(os.environ.get("FLYN_AUTO_MEMORY_DIR",
                                               str(Path.home() / ".claude" / "projects" / "-Users-4c-AI" / "memory")))
        ol_wiki_url = os.environ.get("FLYN_OL_WIKI_URL", "http://localhost:8200")
        ol_wiki_pin = os.environ.get("FLYN_OL_WIKI_PIN", "1080")
        return cls(home=home, workspace=workspace, port=port,
                   passthrough_mode=passthrough, graphiti_url=graphiti_url,
                   knowledge_dir=knowledge_dir, reference_vault=reference_vault,
                   auto_memory_dir=auto_memory_dir, ol_wiki_url=ol_wiki_url,
                   ol_wiki_pin=ol_wiki_pin)


@dataclass(frozen=True)
class ReadSourceConfig:
    name: str
    cls_path: str
    timeout: float
    default_included: bool


READ_SOURCES: dict[str, ReadSourceConfig] = {
    "hot":       ReadSourceConfig("hot",       "flyn_memory_router.adapters.hot_read:HotRead",             1.0, True),
    "warm":      ReadSourceConfig("warm",      "flyn_memory_router.adapters.warm_read:WarmRead",           2.0, True),
    "cool":      ReadSourceConfig("cool",      "flyn_memory_router.adapters.cool_read:CoolRead",           1.0, True),
    "cold":      ReadSourceConfig("cold",      "flyn_memory_router.adapters.cold_read:ColdRead",           1.0, True),
    "lesson":    ReadSourceConfig("lesson",    "flyn_memory_router.adapters.lesson_read:LessonRead",       1.0, True),
    "reference": ReadSourceConfig("reference", "flyn_memory_router.adapters.reference_read:ReferenceRead", 1.5, True),
    "user":      ReadSourceConfig("user",      "flyn_memory_router.adapters.user_read:UserRead",           1.0, True),
    "ol_wiki":   ReadSourceConfig("ol_wiki",   "flyn_memory_router.adapters.ol_wiki_read:OLWikiRead",      2.0, True),
    "ocw_mem":   ReadSourceConfig("ocw_mem",   "flyn_memory_router.adapters.ocw_mem_read:OCWMemRead",      3.0, False),
    "lossless":  ReadSourceConfig("lossless",  "flyn_memory_router.adapters.lossless_read:LosslessRead",   3.0, False),
}
