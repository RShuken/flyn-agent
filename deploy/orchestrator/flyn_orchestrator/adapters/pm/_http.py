"""Shared urllib-based HTTP helper for PM adapters.

Uses stdlib only — no new dependencies.  Supports method, url, json payload,
timeout, and optional custom headers.
"""
from __future__ import annotations

import json as _json
import urllib.request
from typing import Any, Optional


class _Response:
    """Thin wrapper around urllib response to expose `.json()` and `.status_code`."""

    def __init__(self, body: bytes, status: int) -> None:
        self._body = body
        self._status = status

    def json(self) -> Any:
        return _json.loads(self._body)

    @property
    def status_code(self) -> int:
        return self._status


def default_http(
    *,
    method: str,
    url: str,
    json: Any,
    timeout: int = 5,
    headers: Optional[dict[str, str]] = None,
) -> _Response:
    """POST (or any method) JSON to *url*, returning a :class:`_Response`.

    Raises ``urllib.error.URLError`` / ``urllib.error.HTTPError`` on network
    failure — callers are expected to catch and swallow in ``except Exception``.
    """
    data = _json.dumps(json).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(
        url, data=data, method=method.upper(), headers=req_headers
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read()
    return _Response(body, resp.status)
