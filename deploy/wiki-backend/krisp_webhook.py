"""Krisp.ai webhook receiver.

Public endpoint hit by Krisp when a meeting transcript/notes/outline is
generated. Auth via shared-secret header (Krisp doesn't sign requests).
Idempotent by event_id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

import meetings_db


router = APIRouter(prefix="/api/meetings", tags=["meetings"])

# Per-module limiter. In production this shares in-memory state only within
# this process; it cannot share state with the main app.py limiter across
# workers. Acceptable for a low-volume inbound Krisp hook.
limiter = Limiter(key_func=get_remote_address, default_limits=["30/minute"])


def _expected_token() -> str:
    return os.environ.get("FLYN_KRISP_TOKEN", "")


def _check_token(provided: str | None) -> bool:
    expected = _expected_token()
    if not expected:
        return False
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _event_id_from(payload: dict, raw_body: bytes) -> str:
    """Prefer explicit IDs; fall back to a hash of the body.

    Uses `is not None` rather than truthiness so an explicit
    `event_id=0` or `event_id=""` doesn't silently fall through to
    the hash fallback.
    """
    for key in ("event_id", "id", "uuid"):
        v = payload.get(key)
        if v is not None and v != "":
            return str(v)
    return hashlib.sha256(raw_body).hexdigest()[:32]


@router.post("/krisp")
@limiter.limit("30/minute")
async def receive_krisp(
    request: Request,
    conn: sqlite3.Connection = Depends(meetings_db.get_conn),
    x_ol_krisp_token: str | None = Header(default=None, alias="X-OL-Krisp-Token"),
) -> dict:
    if not _check_token(x_ol_krisp_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bad or missing X-OL-Krisp-Token",
        )

    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    event_id = _event_id_from(payload, raw)

    # NOTE: meetings_db._connect uses isolation_level=None (autocommit).
    # The INSERT below is committed independently of the audit() call that
    # follows. That's intentional: if audit() ever fails, we still want the
    # dedup record to survive so duplicate retries return 200 + duplicate=true
    # instead of re-inserting. The audit log is a best-effort trail, not a
    # transactional partner.
    try:
        conn.execute(
            "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
            (event_id, raw.decode("utf-8", errors="replace")),
        )
        duplicate = False
    except sqlite3.IntegrityError:
        duplicate = True

    meetings_db.audit(
        conn, actor="krisp-webhook",
        action="event_received" if not duplicate else "event_duplicate",
        payload={"event_id": event_id},
    )

    return {"received": True, "event_id": event_id, "duplicate": duplicate}
