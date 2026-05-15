"""LinearPMAdapter skeleton.

MVP scope: if no Linear API key is configured, all methods are no-ops that
return a synthetic external_id. Full implementation arrives when Phase 2
dev workflow needs it — at that point, wrap deploy/wiki-backend/linear_sync.py.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Optional

from ...types import TaskRecord, TaskState


def _load_linear_api_key() -> Optional[str]:
    if v := os.environ.get("LINEAR_API_KEY"):
        return v
    p = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if p.exists():
        try:
            d = json.load(open(p))
            for key in ("linear:default", "linear"):
                if key in d.get("profiles", {}):
                    return d["profiles"][key].get("token")
        except Exception:
            pass
    return None


class LinearPMAdapter:
    name = "linear"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or _load_linear_api_key()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def create_task(self, t: TaskRecord) -> str:
        # MVP: return a synthetic id; Phase 2 will hit Linear API
        return f"linear-stub-{t.task_id}"

    def update_state(self, t: TaskRecord, to_state: TaskState) -> None:
        return  # no-op

    def link_artifact(self, t: TaskRecord, artifact: dict[str, Any]) -> None:
        return  # no-op

    def comment_on_task(self, t: TaskRecord, body: str) -> None:
        return  # no-op
