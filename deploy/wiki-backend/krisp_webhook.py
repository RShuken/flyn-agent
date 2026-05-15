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
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

import meetings_db


router = APIRouter(prefix="/api/meetings", tags=["meetings"])


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
    """Prefer explicit IDs; fall back to a hash of the body."""
    for key in ("event_id", "id", "uuid"):
        v = payload.get(key)
        if v:
            return str(v)
    return hashlib.sha256(raw_body).hexdigest()[:16]


@router.post("/krisp")
async def receive_krisp(
    request: Request,
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

    conn = meetings_db._connect()
    try:
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
    finally:
        conn.close()

    return {"received": True, "event_id": event_id, "duplicate": duplicate}
