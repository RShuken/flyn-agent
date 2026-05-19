# deploy/orchestrator/flyn_orchestrator/backends/noop.py
"""NoopBackend: safe default that satisfies WorkerBackend without touching any LLM.

This is the PRODUCTION DEFAULT backend (FLYN_DEFAULT_BACKEND=noop).

Why: the claude-p backend shares Ryan's Claude Code OAuth token; every worker
invocation consumes a session slot and can log him out of interactive Claude Code
sessions. Noop lets the orchestrator run full end-to-end plumbing tests (state
machine, audit, routing, approval gates) without burning any credentials.

To use a real backend, set FLYN_DEFAULT_BACKEND=claude-p (or codex-exec) in your
launchd plist or shell environment.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import WorkerResult, WorkerBackend
from ..types import WorkerSpec

if TYPE_CHECKING:
    from ..cost import CostTracker

_SUMMARY = (
    "noop backend — no LLM work performed; "
    "set FLYN_DEFAULT_BACKEND to enable real workers"
)


class NoopBackend:
    name = "noop"

    def run(
        self,
        spec: WorkerSpec,
        prompt: str,
        *,
        cost_tracker: Optional["CostTracker"] = None,
    ) -> WorkerResult:
        capture_path = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "backend": "noop",
            "ts": time.time(),
            "note": "no LLM call performed",
            "intent": prompt[:200],
        })
        capture_path.write_text(line + "\n", encoding="utf-8")
        return WorkerResult(
            worker_id=spec.worker_id,
            exit_code=0,
            capture_path=capture_path,
            cost_usd=0.0,
            duration_ms=0,
            changed_files=[],
            summary=_SUMMARY,
        )
