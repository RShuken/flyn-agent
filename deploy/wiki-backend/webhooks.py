"""Webhook delivery for mutation events.

Synchronous best-effort POSTs to all active subscribers. Failures are logged
to webhooks.last_status but do not roll back the originating mutation.

Payload shape:
    {
      "event": "question.answered" | "question.reassigned" | "decision.created",
      "ts": ISO8601 UTC,
      "data": { ... event-specific ... },
      "actor": "<who triggered>"
    }

Signature: HMAC-SHA256 of the raw JSON body using the per-subscription secret,
sent as the X-OL-Webhook-Signature header (hex digest).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float = 5.0) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0  # network error


def _deliver_one(db_path: str, sub_id: int, url: str, secret: str | None,
                 body: bytes) -> int:
    """Deliver one webhook + record status. Opens its own DB connection
    because the originating request's connection is closed by the time
    this runs in a background thread."""
    headers = {"Content-Type": "application/json", "User-Agent": "ol-wiki-webhook/1"}
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-OL-Webhook-Signature"] = sig
    status = _post(url, body, headers)
    own_conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        own_conn.execute(
            "UPDATE webhooks SET last_fired_at = ?, last_status = ? WHERE id = ?",
            (_now_iso(), status, sub_id),
        )
    finally:
        own_conn.close()
    return status


def fire_event(conn: sqlite3.Connection, event: str, actor: str, data: dict[str, Any]) -> int:
    """Fan out an event to every active subscriber whose event_types includes
    this event or wildcard "*". Returns count of subscribers attempted.

    Subscriptions are read synchronously; delivery happens in background
    threads so one slow receiver doesn't block others. Each thread opens
    its own DB connection for the status update (the request connection
    closes too quickly to use from the thread).
    """
    payload = {
        "event": event,
        "ts": _now_iso(),
        "actor": actor,
        "data": data,
    }
    body = json.dumps(payload, sort_keys=True).encode()

    rows = conn.execute(
        "SELECT id, target_url, event_types, secret FROM webhooks WHERE active = 1"
    ).fetchall()

    # The worker threads need to open their own connections to the same DB.
    # PRAGMA database_list returns rows of (seq, name, file).
    db_list = conn.execute("PRAGMA database_list").fetchall()
    db_path = db_list[0][2] if db_list else ":memory:"

    attempted = 0
    for row in rows:
        try:
            types = json.loads(row["event_types"]) if row["event_types"] else []
        except json.JSONDecodeError:
            types = []
        if types and "*" not in types and event not in types:
            continue
        attempted += 1
        threading.Thread(
            target=_deliver_one,
            args=(db_path, row["id"], row["target_url"], row["secret"], body),
            daemon=True,
        ).start()
    return attempted
