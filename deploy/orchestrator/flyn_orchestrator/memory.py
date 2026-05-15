from __future__ import annotations
from typing import Any, Optional, Protocol


class _Http(Protocol):
    def post(self, url: str, *, json: dict[str, Any], timeout: float = ...) -> Any: ...


class MemoryEmitter:
    def __init__(self, router_url: str, http: _Http) -> None:
        self._url = router_url.rstrip("/")
        self._http = http

    def emit(self, *, source: str, event_type: str, subject: str, body: str,
             dedup_key: str, importance: Optional[str] = None,
             raw_payload: Optional[dict[str, Any]] = None) -> None:
        """Best-effort emit. Never raises — router-side issues are notes, not orchestrator-side errors."""
        payload: dict[str, Any] = {
            "source": source, "event_type": event_type, "subject": subject,
            "body": body, "dedup_key": dedup_key,
        }
        if importance:
            payload["importance"] = importance
        if raw_payload:
            payload["raw_payload"] = raw_payload
        try:
            self._http.post(f"{self._url}/api/memory/ingest", json=payload, timeout=10.0)
        except Exception:
            return  # swallow — router-side outages mustn't break the orchestrator
