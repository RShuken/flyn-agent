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
    ol_wiki_pin: str  # auth PIN string (not a port; default "1080" is documented in OL wiki backend)

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

    @property
    def conv2_root(self) -> Path:
        """Conv-tier 2.0 per-owner DBs root. Separate from v1's conv/ during
        shadow-mode rollout."""
        env = os.environ.get("FLYN_CONV2_ROOT")
        if env:
            return Path(env)
        return self.home / "conv2"

    @property
    def conv2_shadow_mode(self) -> bool:
        """When True, /api/memory/ingest writes to BOTH v1 conv and v2 conv2
        for output comparison. Default: True during migration period."""
        return os.environ.get("FLYN_CONV2_SHADOW", "true").lower() == "true"

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
    "hot":       ReadSourceConfig(name="hot",       cls_path="flyn_memory_router.adapters.hot_read:HotRead",             timeout=1.0, default_included=True),
    "warm":      ReadSourceConfig(name="warm",      cls_path="flyn_memory_router.adapters.warm_read:WarmRead",           timeout=2.0, default_included=True),
    "cool":      ReadSourceConfig(name="cool",      cls_path="flyn_memory_router.adapters.cool_read:CoolRead",           timeout=1.0, default_included=True),
    "cold":      ReadSourceConfig(name="cold",      cls_path="flyn_memory_router.adapters.cold_read:ColdRead",           timeout=1.0, default_included=True),
    "lesson":    ReadSourceConfig(name="lesson",    cls_path="flyn_memory_router.adapters.lesson_read:LessonRead",       timeout=1.0, default_included=True),
    "reference": ReadSourceConfig(name="reference", cls_path="flyn_memory_router.adapters.reference_read:ReferenceRead", timeout=1.5, default_included=True),
    "user":      ReadSourceConfig(name="user",      cls_path="flyn_memory_router.adapters.user_read:UserRead",           timeout=1.0, default_included=True),
    "ol_wiki":   ReadSourceConfig(name="ol_wiki",   cls_path="flyn_memory_router.adapters.ol_wiki_read:OLWikiRead",      timeout=2.0, default_included=True),
    "ocw_mem":   ReadSourceConfig(name="ocw_mem",   cls_path="flyn_memory_router.adapters.ocw_mem_read:OCWMemRead",      timeout=3.0, default_included=False),
    "lossless":  ReadSourceConfig(name="lossless",  cls_path="flyn_memory_router.adapters.lossless_read:LosslessRead",   timeout=3.0, default_included=False),
}
