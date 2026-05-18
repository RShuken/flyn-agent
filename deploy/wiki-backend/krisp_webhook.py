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


def _get(d: dict, *keys: str, default: Any = None) -> Any:
    """Try several keys; first non-None wins. Tolerates Krisp's
    not-yet-fully-known field naming variants."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _meeting_obj(payload: dict) -> dict:
    """Krisp's real `note_generated` event nests meeting under `data.meeting`;
    our older spec fixtures use top-level `meeting`. Tolerate both."""
    if isinstance(payload.get("meeting"), dict):
        return payload["meeting"]
    data = payload.get("data") or {}
    if isinstance(data.get("meeting"), dict):
        return data["meeting"]
    return {}


def _normalize_attendees(items: Any) -> list[dict]:
    """Krisp's `participants`/`speakers` arrays have split `first_name`/
    `last_name` with no combined `name`. Backfill `name` so downstream
    callers (Telegram pings, categorizer) don't need to know the source."""
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("name"):
            out.append(it)
            continue
        first = it.get("first_name") or ""
        last = it.get("last_name") or ""
        combined = " ".join(p for p in (first, last) if p).strip()
        if combined:
            merged = dict(it)
            merged["name"] = combined
            out.append(merged)
        else:
            out.append(it)
    return out


def _extract_meeting_fields(payload: dict) -> dict[str, Any]:
    """Best-effort map from Krisp's payload to our meetings columns.

    Defensive: every field is optional. Unknown shapes leave columns NULL.
    The transcript_text / notes_text / outline_text / key_points_text
    columns are only populated when their respective event-type sub-object
    is present, so multiple events for the same meeting merge cleanly.
    """
    meeting = _meeting_obj(payload)
    out: dict[str, Any] = {
        "meeting_id": _get(meeting, "id", "meeting_id", "uuid"),
        "title": _get(meeting, "title", "name"),
        "started_at": _get(meeting, "started_at", "start_time", "start", "start_date"),
        "ended_at": _get(meeting, "ended_at", "end_time", "end", "end_date"),
        "duration_seconds": _get(meeting, "duration_seconds", "duration"),
        "meeting_url": _get(meeting, "url", "link", "meeting_url"),
        "attendees": json.dumps(_normalize_attendees(
            _get(meeting, "attendees", "participants", "speakers", default=[])
        )),
    }
    # Content sub-objects — each event may carry one of these.
    if "transcript" in payload and isinstance(payload["transcript"], dict):
        out["transcript_text"] = _get(payload["transcript"], "text", "content", "body")
    if "notes" in payload and isinstance(payload["notes"], dict):
        out["notes_text"] = _get(payload["notes"], "text", "content", "body")
    if "outline" in payload and isinstance(payload["outline"], dict):
        out["outline_text"] = _get(payload["outline"], "text", "content", "body")
    if "key_points" in payload and isinstance(payload["key_points"], dict):
        out["key_points_text"] = _get(payload["key_points"], "text", "content", "body")
    # Krisp's `note_generated` event ships markdown notes under data.raw_content;
    # no per-section sub-object like our spec fixtures had.
    data = payload.get("data") or {}
    if isinstance(data.get("raw_content"), str):
        out["notes_text"] = data["raw_content"]
    return out


def _upsert_meeting(conn: sqlite3.Connection, fields: dict[str, Any]) -> None:
    """UPSERT keyed on meeting_id. NULL incoming values do NOT overwrite
    existing populated values (so a later 'notes' event doesn't clobber
    the title set by an earlier 'transcript' event)."""
    meeting_id = fields.get("meeting_id")
    if not meeting_id:
        return  # nothing to do without an ID

    existing = conn.execute(
        "SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,)
    ).fetchone()

    if existing is None:
        cols = [k for k, v in fields.items() if v is not None]
        vals = [fields[k] for k in cols]
        placeholders = ",".join(["?"] * len(cols))
        conn.execute(
            f"INSERT INTO meetings ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        return

    # Merge: only set columns where existing is NULL/empty and incoming is not.
    set_parts = []
    set_vals: list[Any] = []
    for k, v in fields.items():
        if k == "meeting_id" or v is None:
            continue
        if existing[k] in (None, "", "[]"):
            set_parts.append(f"{k} = ?")
            set_vals.append(v)
    if set_parts:
        set_parts.append("updated_at = datetime('now')")
        set_vals.append(meeting_id)
        conn.execute(
            f"UPDATE meetings SET {', '.join(set_parts)} WHERE meeting_id = ?",
            set_vals,
        )


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
            "INSERT INTO meeting_events (event_id, source, event_type, "
            "meeting_id, raw_payload) VALUES (?, ?, ?, ?, ?)",
            (
                event_id,
                "krisp",
                str(payload.get("event_type") or payload.get("event") or payload.get("type") or ""),
                str(_meeting_obj(payload).get("id") or ""),
                raw.decode("utf-8", errors="replace"),
            ),
        )
        duplicate = False
    except sqlite3.IntegrityError:
        duplicate = True

    if not duplicate:
        fields = _extract_meeting_fields(payload)
        _upsert_meeting(conn, fields)

    meetings_db.audit(
        conn, actor="krisp-webhook",
        action="event_received" if not duplicate else "event_duplicate",
        meeting_id=(payload.get("meeting") or {}).get("id"),
        payload={"event_id": event_id},
    )

    return {"received": True, "event_id": event_id, "duplicate": duplicate}
