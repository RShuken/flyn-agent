"""Discovery-artifact writers used by install.sh. All idempotent."""
from __future__ import annotations

from pathlib import Path

AUTO_MEMORY_FILE = "feedback_memory_router.md"

AUTO_MEMORY_BODY = """---
name: memory-router-front-door
description: Cross-system memory queries on this Mac route through `flyn-mem` CLI (or POST :8400/api/memory/query). Spans Flyn workspace, Graphiti, OpenClaw memory, Karpathy vault, auto-memory, ol-wiki.
metadata:
  type: reference
---
For any "what does Ryan know about X" question, prefer `flyn-mem query "X"` before
filesystem grep or per-source reads. Returns ranked hits + citations across 10 sources.

Quick examples:
  flyn-mem query "who is Beth?"                  # all sources, top 10
  flyn-mem query "Flyn memory schema" --include reference lesson
  flyn-mem query "..." --exclude lossless ocw_mem
  flyn-mem sources                                # per-adapter health
  flyn-mem logs --query-id <id>                   # debug a result

Service runs at localhost:8400 (launchd: ai.flyn.memory-router).
If `flyn-mem` is missing: see ~/AI/openclaw/flyn-agent/deploy/memory-router/README.md
"""

MEMORY_MD_INDEX_LINE = "- [memory-router-front-door](feedback_memory_router.md) — flyn-mem CLI for cross-system queries\n"

TOOLS_MD_SECTION = """
## flyn-mem (memory router)

REST: `http://127.0.0.1:8400/api/memory/{query,ingest,lint,sources}`
CLI: `flyn-mem query "<q>"` / `flyn-mem health` / `flyn-mem logs --query-id <id>`

Use `flyn-mem query` before grepping workspace files; it fans out across
hot/warm/cool/cold/lesson/reference/user/ol_wiki sources with RRF rank fusion.
"""


def write_auto_memory_pointer(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / AUTO_MEMORY_FILE
    if not target.exists():
        target.write_text(AUTO_MEMORY_BODY)


def append_memory_md_index(memory_dir: Path) -> None:
    idx = memory_dir / "MEMORY.md"
    if not idx.exists():
        idx.write_text(MEMORY_MD_INDEX_LINE)
        return
    text = idx.read_text()
    if AUTO_MEMORY_FILE in text:
        return
    with idx.open("a") as f:
        f.write(MEMORY_MD_INDEX_LINE)


def append_tools_md(workspace_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    tools = workspace_dir / "TOOLS.md"
    if tools.exists():
        text = tools.read_text()
        if "## flyn-mem" in text:
            return
        with tools.open("a") as f:
            f.write(TOOLS_MD_SECTION)
    else:
        tools.write_text("# TOOLS\n" + TOOLS_MD_SECTION)
