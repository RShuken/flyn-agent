"""Warm-tier adapters: writes one Graphiti episode + one workspace/memory/*.md file per event.

Per spec §2.5: only prose `body` goes to Graphiti — never raw structured dumps.
Group_id is hardcoded to `flyn` upstream in the Graphiti REST wrapper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..redact import redact
from ..types import InboundEvent
from .base import WriteResult


class _HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, Any]) -> Any: ...


class WarmGraphitiAdapter:
    name = "warm.graphiti"

    def __init__(self, graphiti_url: str, http: _HttpClient) -> None:
        self._url = graphiti_url.rstrip("/")
        self._http = http

    def write(self, event: InboundEvent) -> WriteResult:
        # Prose body only, redacted. group_id is hardcoded upstream.
        episode_name = f"{event.subject} | {event.event_type}"[:128]
        payload = {"name": episode_name, "body": redact(event.body)}
        try:
            resp = self._http.post(f"{self._url}/api/episode", json=payload)
        except Exception as ex:  # noqa: BLE001
            return WriteResult(target=self.name, ok=False,
                               detail=f"transport: {type(ex).__name__}: {ex!s}"[:200])
        status = getattr(resp, "status_code", None)
        if status and 200 <= status < 300:
            return WriteResult(target=self.name, ok=True, detail=f"graphiti {status}")
        body_text = getattr(resp, "text", "")[:200]
        return WriteResult(target=self.name, ok=False, detail=f"graphiti {status}: {body_text}")


class WarmWorkspaceFileAdapter:
    name = "warm.workspace_file"

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def write(self, event: InboundEvent) -> WriteResult:
        ts = datetime.now(timezone.utc)
        date = ts.strftime("%Y-%m-%d")
        # one file per (date, subject) — multiple events on same subject same day append
        safe_subject = "".join(c if c.isalnum() or c in "-_" else "-" for c in event.subject)[:64]
        path = self._dir / f"{date}-{safe_subject}.md"
        existing = path.read_text() if path.exists() else f"# {event.subject}\n\n"
        addition = (
            f"\n## {ts.isoformat()} — {event.source} / {event.event_type}\n\n"
            f"{redact(event.body)}\n"
        )
        path.write_text(existing + addition)
        return WriteResult(target=self.name, ok=True, detail=f"appended -> {path.name}")
