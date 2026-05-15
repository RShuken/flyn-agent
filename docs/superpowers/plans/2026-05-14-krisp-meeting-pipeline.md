# Krisp Meeting Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Krisp.ai webhook receiver, a Flyn-wide meeting inbox, and a nightly categorizer that auto-routes meeting transcripts to the right project (with a /route Telegram command for the unclassifiable ones).

**Architecture:** Three subsystems on top of the existing wiki-backend FastAPI (port 8200, Tailscale-Funnel'd): (1) `POST /api/meetings/krisp` with shared-secret header auth, idempotent by event_id, (2) new SQLite file `flyn-meetings.db` separate from `ol-pm.db`, (3) nightly cron classifier using attendee/title rules then `claude -p` fallback, with unclassified meetings surfacing in the morning Telegram digest.

**Tech Stack:** Python 3.14, FastAPI 0.110+, Pydantic 2.5+, SQLite, slowapi, launchd, `claude -p` CLI (subscription-billed, no API key), existing openclaw gateway for Telegram I/O.

**Spec:** `docs/superpowers/specs/2026-05-14-krisp-webhook-design.md`

---

## Phase 1 — Meeting Inbox (DB + models)

### Task 1: Create meetings_db.py with schema + idempotent init

**Files:**
- Create: `deploy/wiki-backend/meetings_db.py`
- Create: `deploy/wiki-backend/tests/test_meetings_db.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/wiki-backend/tests/test_meetings_db.py
"""Tests for the Flyn-wide meeting inbox SQLite layer."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import meetings_db as mdb  # noqa: E402


def test_init_creates_tables():
    mdb.init_db()
    conn = mdb._connect()
    try:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"meeting_events", "meetings", "meeting_audit"}.issubset(names)
    finally:
        conn.close()


def test_init_is_idempotent():
    mdb.init_db()
    mdb.init_db()  # second call must not raise
    conn = mdb._connect()
    try:
        # Insert a row, confirm second init didn't wipe data.
        conn.execute(
            "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
            ("ev-1", "{}"),
        )
        mdb.init_db()
        n = conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_event_id_unique_constraint():
    mdb.init_db()
    conn = mdb._connect()
    try:
        conn.execute(
            "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
            ("ev-dup", "{}"),
        )
        with pytest.raises(Exception):  # IntegrityError
            conn.execute(
                "INSERT INTO meeting_events (event_id, raw_payload) VALUES (?, ?)",
                ("ev-dup", "{}"),
            )
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_meetings_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meetings_db'`

- [ ] **Step 3: Write meetings_db.py**

```python
# deploy/wiki-backend/meetings_db.py
"""SQLite layer for the Flyn-wide meeting inbox.

Lives next to db.py (OL wiki) but uses a separate file (~/.openclaw/data/
flyn-meetings.db) so meeting data is logically partitioned from OL-specific
state. Same patterns: idempotent CREATE TABLE IF NOT EXISTS, WAL mode,
one connection per request via FastAPI dependency.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

DB_PATH = Path(os.environ.get(
    "FLYN_MEETINGS_DB",
    str(Path.home() / ".openclaw" / "data" / "flyn-meetings.db"),
))

_init_lock = threading.Lock()
_initialized = False


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS meeting_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT    NOT NULL UNIQUE,
        received_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        source       TEXT    NOT NULL DEFAULT 'krisp',
        event_type   TEXT,
        meeting_id   TEXT,
        raw_payload  TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meetings (
        meeting_id        TEXT PRIMARY KEY,
        title             TEXT,
        started_at        TEXT,
        ended_at          TEXT,
        duration_seconds  INTEGER,
        meeting_url       TEXT,
        attendees         TEXT    NOT NULL DEFAULT '[]',
        transcript_text   TEXT,
        notes_text        TEXT,
        outline_text      TEXT,
        key_points_text   TEXT,
        status            TEXT    NOT NULL DEFAULT 'pending',
        routed_project    TEXT,
        routed_commit_sha TEXT,
        classifier_reason TEXT,
        classifier_confidence TEXT,
        first_seen_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        routed_at         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meeting_audit (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT    NOT NULL DEFAULT (datetime('now')),
        meeting_id TEXT,
        actor      TEXT    NOT NULL,
        action     TEXT    NOT NULL,
        payload    TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_meeting ON meeting_events(meeting_id)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_started ON meetings(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_meeting ON meeting_audit(meeting_id)",
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = _connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            for stmt in SCHEMA:
                conn.execute(stmt)
        finally:
            conn.close()
        _initialized = True


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str,
          meeting_id: str | None = None, payload: str = "{}") -> None:
    conn.execute(
        "INSERT INTO meeting_audit (meeting_id, actor, action, payload) "
        "VALUES (?, ?, ?, ?)",
        (meeting_id, actor, action, payload),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_meetings_db.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add deploy/wiki-backend/meetings_db.py deploy/wiki-backend/tests/test_meetings_db.py
git commit -m "feat(meetings): SQLite layer for Flyn-wide meeting inbox

Three tables: meeting_events (raw payloads, idempotent by event_id),
meetings (one row per Meeting ID, status-tracked), meeting_audit.
Separate DB file (~/.openclaw/data/flyn-meetings.db) keeps Flyn-wide
meeting state out of OL-specific ol-pm.db.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pydantic models for webhook + meeting

**Files:**
- Modify: `deploy/wiki-backend/models.py` (append)
- Create: `deploy/wiki-backend/tests/test_meeting_models.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/wiki-backend/tests/test_meeting_models.py
"""Pydantic model validation for meeting payloads."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import MeetingRow, KrispWebhookAck  # noqa: E402


def test_meeting_row_minimal():
    m = MeetingRow(meeting_id="m-1")
    assert m.meeting_id == "m-1"
    assert m.status == "pending"
    assert m.attendees == []


def test_meeting_row_full():
    m = MeetingRow(
        meeting_id="m-1",
        title="Sprint sync",
        attendees=[{"name": "Beth", "email": "beth@example.com"}],
        transcript_text="hello",
        status="routed",
    )
    assert m.attendees[0]["email"] == "beth@example.com"


def test_krisp_ack_shape():
    ack = KrispWebhookAck(received=True, event_id="ev-1")
    assert ack.received is True
    assert ack.duplicate is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_meeting_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'MeetingRow'`

- [ ] **Step 3: Append models**

Add to the end of `deploy/wiki-backend/models.py`:

```python
# --- Meeting inbox models -------------------------------------------------


class MeetingRow(BaseModel):
    """One row in the meetings table."""
    meeting_id: str
    title: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: int | None = None
    meeting_url: str | None = None
    attendees: list[dict] = Field(default_factory=list)
    transcript_text: str | None = None
    notes_text: str | None = None
    outline_text: str | None = None
    key_points_text: str | None = None
    status: str = "pending"
    routed_project: str | None = None
    routed_commit_sha: str | None = None
    classifier_reason: str | None = None
    classifier_confidence: str | None = None


class KrispWebhookAck(BaseModel):
    """Response we send back to Krisp."""
    received: bool
    event_id: str
    duplicate: bool = False
```

(If `Field` isn't already imported at the top of `models.py`, add it: `from pydantic import BaseModel, Field`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_meeting_models.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add deploy/wiki-backend/models.py deploy/wiki-backend/tests/test_meeting_models.py
git commit -m "feat(meetings): Pydantic models for meeting row + Krisp ack

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Krisp Webhook Receiver

### Task 3: Webhook auth + 200 skeleton

**Files:**
- Create: `deploy/wiki-backend/krisp_webhook.py`
- Create: `deploy/wiki-backend/tests/test_krisp_webhook.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/wiki-backend/tests/test_krisp_webhook.py
"""Tests for the Krisp webhook receiver."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmpwiki = tempfile.NamedTemporaryFile(suffix="-ol.db", delete=False)
_tmpwiki.close()
_tmpmeet = tempfile.NamedTemporaryFile(suffix="-meet.db", delete=False)
_tmpmeet.close()
os.environ["OL_WIKI_DB"] = _tmpwiki.name
os.environ["FLYN_MEETINGS_DB"] = _tmpmeet.name
os.environ["OL_WIKI_API_KEY"] = "test-key"
os.environ["FLYN_KRISP_TOKEN"] = "krisp-test-token"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from db import init_db as init_ol_db  # noqa: E402
from meetings_db import init_db as init_meet_db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_ol_db()
    init_meet_db()
    with TestClient(app) as c:
        yield c


def test_missing_token_returns_401(client):
    r = client.post("/api/meetings/krisp", json={"event_id": "x"})
    assert r.status_code == 401


def test_wrong_token_returns_401(client):
    r = client.post(
        "/api/meetings/krisp",
        json={"event_id": "x"},
        headers={"X-OL-Krisp-Token": "wrong"},
    )
    assert r.status_code == 401


def test_valid_token_returns_200(client):
    r = client.post(
        "/api/meetings/krisp",
        json={"event_id": "ev-001"},
        headers={"X-OL-Krisp-Token": "krisp-test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["event_id"] == "ev-001"
    assert body["duplicate"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_krisp_webhook.py -v`
Expected: FAIL — module import error or 404 (route not registered yet).

- [ ] **Step 3: Write krisp_webhook.py (skeleton)**

```python
# deploy/wiki-backend/krisp_webhook.py
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
            payload=json.dumps({"event_id": event_id}),
        )
    finally:
        conn.close()

    return {"received": True, "event_id": event_id, "duplicate": duplicate}
```

- [ ] **Step 4: Wire the router into app.py (just enough for this task)**

In `deploy/wiki-backend/app.py`, near the other route registrations (just before the existing endpoints or after `app.add_middleware(...)`), add:

```python
from krisp_webhook import router as krisp_router  # noqa: E402

app.include_router(krisp_router)
```

Also, in the app's startup (find the existing `init_db()` call), add `meetings_db.init_db()` next to it. If startup is lifespan-based, add it there; if it's a simple call at the bottom of the file or in a `@app.on_event("startup")` handler, add it there.

Search for `init_db()` in `app.py`:

```bash
grep -n "init_db" /Users/4c/AI/flyn-agent/deploy/wiki-backend/app.py
```

Wherever the existing call lives, add right after it:

```python
import meetings_db
meetings_db.init_db()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_krisp_webhook.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add deploy/wiki-backend/krisp_webhook.py deploy/wiki-backend/app.py deploy/wiki-backend/tests/test_krisp_webhook.py
git commit -m "feat(krisp): webhook receiver skeleton with shared-secret auth

POST /api/meetings/krisp: validate X-OL-Krisp-Token (constant-time),
parse JSON, idempotent insert into meeting_events keyed by event_id
(or sha256 of body if none provided). Audit-logged. Returns 200 +
{received, event_id, duplicate}.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Payload extraction + UPSERT into meetings table

**Files:**
- Modify: `deploy/wiki-backend/krisp_webhook.py`
- Modify: `deploy/wiki-backend/tests/test_krisp_webhook.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_krisp_webhook.py`:

```python
def test_payload_extracts_meeting_and_upserts(client):
    payload = {
        "event_id": "ev-merge-1",
        "event_type": "transcript.created",
        "meeting": {
            "id": "mtg-42",
            "title": "Sprint sync",
            "url": "https://krisp.ai/m/mtg-42",
            "started_at": "2026-05-14T15:00:00Z",
            "ended_at": "2026-05-14T15:28:00Z",
            "duration_seconds": 1680,
            "attendees": [
                {"name": "Beth", "email": "beth@example.com"},
                {"name": "Ryan", "email": "ryanshuken@gmail.com"},
            ],
        },
        "transcript": {"text": "hello world"},
    }
    r = client.post(
        "/api/meetings/krisp", json=payload,
        headers={"X-OL-Krisp-Token": "krisp-test-token"},
    )
    assert r.status_code == 200

    import sqlite3
    conn = sqlite3.connect(os.environ["FLYN_MEETINGS_DB"])
    row = conn.execute(
        "SELECT title, transcript_text, attendees, status "
        "FROM meetings WHERE meeting_id = ?",
        ("mtg-42",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "Sprint sync"
    assert row[1] == "hello world"
    assert "beth@example.com" in row[2]
    assert row[3] == "pending"


def test_second_event_merges_into_same_meeting(client):
    # First event: transcript
    p1 = {
        "event_id": "ev-mrg-a",
        "event_type": "transcript.created",
        "meeting": {"id": "mtg-merge", "title": "T"},
        "transcript": {"text": "T-text"},
    }
    client.post("/api/meetings/krisp", json=p1,
                headers={"X-OL-Krisp-Token": "krisp-test-token"})
    # Second event: notes for same meeting
    p2 = {
        "event_id": "ev-mrg-b",
        "event_type": "notes.generated",
        "meeting": {"id": "mtg-merge", "title": "T"},
        "notes": {"text": "N-text"},
    }
    client.post("/api/meetings/krisp", json=p2,
                headers={"X-OL-Krisp-Token": "krisp-test-token"})

    import sqlite3
    conn = sqlite3.connect(os.environ["FLYN_MEETINGS_DB"])
    row = conn.execute(
        "SELECT transcript_text, notes_text FROM meetings WHERE meeting_id = ?",
        ("mtg-merge",),
    ).fetchone()
    conn.close()
    assert row[0] == "T-text"
    assert row[1] == "N-text"


def test_duplicate_event_id_returns_duplicate_true(client):
    p = {"event_id": "ev-dup", "meeting": {"id": "mtg-dup"}}
    r1 = client.post("/api/meetings/krisp", json=p,
                     headers={"X-OL-Krisp-Token": "krisp-test-token"})
    r2 = client.post("/api/meetings/krisp", json=p,
                     headers={"X-OL-Krisp-Token": "krisp-test-token"})
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_krisp_webhook.py -v -k "extracts or merges or duplicate_event"`
Expected: FAIL (rows aren't being upserted into `meetings`).

- [ ] **Step 3: Add the extractor + UPSERT logic to krisp_webhook.py**

Add these helpers near the top of `krisp_webhook.py`, before the route handler:

```python
def _get(d: dict, *keys: str, default: Any = None) -> Any:
    """Try several keys; first non-None wins. Tolerates Krisp's
    not-yet-fully-known field naming variants."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _extract_meeting_fields(payload: dict) -> dict[str, Any]:
    """Best-effort map from Krisp's payload to our meetings columns.

    Defensive: every field is optional. Unknown shapes leave columns NULL.
    The transcript_text / notes_text / outline_text / key_points_text
    columns are only populated when their respective event-type sub-object
    is present, so multiple events for the same meeting merge cleanly.
    """
    meeting = payload.get("meeting") or {}
    out: dict[str, Any] = {
        "meeting_id": _get(meeting, "id", "meeting_id", "uuid"),
        "title": _get(meeting, "title", "name"),
        "started_at": _get(meeting, "started_at", "start_time", "start"),
        "ended_at": _get(meeting, "ended_at", "end_time", "end"),
        "duration_seconds": _get(meeting, "duration_seconds", "duration"),
        "meeting_url": _get(meeting, "url", "link", "meeting_url"),
        "attendees": json.dumps(_get(meeting, "attendees", "participants", default=[])),
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
```

Then modify the route handler. Replace the body block (between `conn = meetings_db._connect()` and the return) with:

```python
    conn = meetings_db._connect()
    try:
        try:
            conn.execute(
                "INSERT INTO meeting_events (event_id, source, event_type, "
                "meeting_id, raw_payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event_id,
                    "krisp",
                    str(payload.get("event_type") or payload.get("type") or ""),
                    str((payload.get("meeting") or {}).get("id") or ""),
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
            payload=json.dumps({"event_id": event_id}),
        )
    finally:
        conn.close()

    return {"received": True, "event_id": event_id, "duplicate": duplicate}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/test_krisp_webhook.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/wiki-backend/krisp_webhook.py deploy/wiki-backend/tests/test_krisp_webhook.py
git commit -m "feat(krisp): extract + UPSERT meeting fields with multi-event merge

Defensive _get() tolerates Krisp's not-yet-fully-known field names.
UPSERT preserves existing populated values, so notes/outline events
arriving after a transcript don't clobber the title or start time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Verify wiring + service reload smoke

**Files:** (no code changes; operational verification only)

- [ ] **Step 1: Run all wiki-backend tests**

Run: `cd /Users/4c/AI/flyn-agent/deploy/wiki-backend && .venv/bin/pytest tests/ -v`
Expected: all pass (existing OL wiki tests + new meeting tests).

- [ ] **Step 2: Local-curl smoke (service NOT yet reloaded)**

Set a token and run the server in foreground for one-off testing:

```bash
cd /Users/4c/AI/flyn-agent/deploy/wiki-backend
FLYN_KRISP_TOKEN=local-dev-token \
OL_WIKI_API_KEY="$(python3 -c 'import json; print(json.load(open("/Users/4c/.openclaw/openclaw.json"))["channels"]["telegram"]["botToken"])')" \
.venv/bin/uvicorn app:app --port 8201 &
sleep 2
curl -sS -X POST http://127.0.0.1:8201/api/meetings/krisp \
  -H "X-OL-Krisp-Token: local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"event_id":"smoke-1","meeting":{"id":"smk-mtg","title":"Smoke"},"transcript":{"text":"hi"}}'
echo
sqlite3 ~/.openclaw/data/flyn-meetings.db \
  "SELECT meeting_id, title, transcript_text, status FROM meetings WHERE meeting_id='smk-mtg'"
kill %1
```

Expected: `{"received":true,"event_id":"smoke-1","duplicate":false}` and DB row with `smk-mtg | Smoke | hi | pending`.

- [ ] **Step 3: No commit** (verification only)

---

## Phase 3 — Routing helper

### Task 6: Extract route_meeting_to_project into _lib.py

**Files:**
- Modify: `deploy/pm/_lib.py` (append)
- Create: `deploy/pm/tests/test_route_meeting.py`
- Modify: `deploy/pm/tests/__init__.py` (create if missing)

- [ ] **Step 1: Ensure pm tests directory exists**

```bash
mkdir -p /Users/4c/AI/flyn-agent/deploy/pm/tests
touch /Users/4c/AI/flyn-agent/deploy/pm/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# deploy/pm/tests/test_route_meeting.py
"""Tests for route_meeting_to_project() in _lib."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import route_meeting_to_project, ProjectConfig  # noqa: E402


@pytest.fixture
def fake_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKLOG.md").write_text("# WORKLOG\n")
    return repo


def _fake_cfg(repo_path: Path) -> ProjectConfig:
    return ProjectConfig(slug="testproj", raw={
        "display_name": "Test",
        "repo": {"path": str(repo_path), "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": "Ryan Shuken", "role": "dev", "side": "us",
             "primary_channel": "telegram", "chat_id": "7191564227"},
        ],
        "cadence": {"morning_standup": {"recipients": ["Ryan Shuken"]}},
    })


def test_route_writes_transcript_and_commits(fake_repo):
    meeting = {
        "meeting_id": "mtg-1",
        "title": "Sprint sync",
        "started_at": "2026-05-14T15:00:00Z",
        "attendees": [{"name": "Beth", "email": "beth@example.com"}],
        "transcript_text": "hello\nworld",
        "notes_text": None,
        "meeting_url": "https://krisp.ai/m/mtg-1",
    }
    cfg = _fake_cfg(fake_repo)

    with patch("_lib.git_pull") as pull, \
         patch("_lib.git_commit_and_push", return_value="abc1234") as push, \
         patch("_lib.graphiti_episode", return_value={"ok": True}) as graph, \
         patch("_lib.telegram_send") as tg:
        result = route_meeting_to_project(meeting, cfg)

    assert result["commit_sha"] == "abc1234"
    pull.assert_called_once()
    push.assert_called_once()
    graph.assert_called_once()
    assert tg.called  # at least one operator notified

    written = list(fake_repo.glob("docs/00-source/meetings/*/transcript.md"))
    assert len(written) == 1
    body = written[0].read_text()
    assert "hello" in body
    assert "beth@example.com" in body
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && .venv/bin/pytest tests/test_route_meeting.py -v 2>/dev/null || python3 -m pytest tests/test_route_meeting.py -v`

(If pm/ has no .venv, use the wiki-backend one: `/Users/4c/AI/flyn-agent/deploy/wiki-backend/.venv/bin/pytest`. If pytest isn't installed there, run `pip install pytest` into that venv first.)

Expected: FAIL with `ImportError: cannot import name 'route_meeting_to_project'`.

- [ ] **Step 4: Append route_meeting_to_project to _lib.py**

Append to `deploy/pm/_lib.py`:

```python
# --- Meeting routing ------------------------------------------------------

from datetime import datetime
import re


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "untitled").lower()).strip("-")
    return s[:max_len] or "untitled"


def _meeting_date(started_at: str | None) -> str:
    if not started_at:
        return datetime.utcnow().strftime("%Y-%m-%d")
    try:
        return started_at[:10]  # ISO 8601 prefix
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


def route_meeting_to_project(meeting: dict, cfg: ProjectConfig) -> dict:
    """Write meeting content into a project repo, commit, push, ingest, ping.

    `meeting` is a dict matching the meetings table columns (meeting_id,
    title, started_at, attendees, transcript_text, notes_text, etc.).
    Returns {"commit_sha": str, "target_rel": str}.
    """
    date = _meeting_date(meeting.get("started_at"))
    slug = _slugify(meeting.get("title") or meeting.get("meeting_id") or "")
    target_rel = f"docs/00-source/meetings/{date}_{slug}/transcript.md"
    target = cfg.repo_path / target_rel

    git_pull(cfg.repo_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    attendees = meeting.get("attendees") or []
    if isinstance(attendees, str):
        try:
            attendees = json.loads(attendees)
        except json.JSONDecodeError:
            attendees = []

    header = (
        f"# {meeting.get('title') or 'Meeting'}\n\n"
        f"- **Date:** {date}\n"
        f"- **Meeting ID:** {meeting.get('meeting_id', '')}\n"
        f"- **URL:** {meeting.get('meeting_url') or '(none)'}\n"
        f"- **Duration:** {meeting.get('duration_seconds') or '?'}s\n"
        f"- **Attendees:** {', '.join(a.get('email') or a.get('name') or '?' for a in attendees) or '(none listed)'}\n"
        f"- **Source:** krisp\n\n---\n\n"
    )
    target.write_text(header + (meeting.get("transcript_text") or "(no transcript)") + "\n")

    paths_to_commit = [target_rel]
    for kind, col in (("notes", "notes_text"), ("outline", "outline_text"),
                      ("key_points", "key_points_text")):
        if meeting.get(col):
            extra = target.parent / f"{kind}.md"
            extra.write_text(f"# {kind.replace('_', ' ').title()}\n\n{meeting[col]}\n")
            paths_to_commit.append(str(extra.relative_to(cfg.repo_path)))

    # WORKLOG entry
    worklog = cfg.repo_path / "WORKLOG.md"
    if worklog.exists():
        line = f"\n- {date}: meeting `{slug}` filed at `{target_rel}` (Flyn auto-route)\n"
        worklog.write_text(worklog.read_text() + line)
        paths_to_commit.append("WORKLOG.md")

    sha = git_commit_and_push(
        cfg.repo_path, paths=paths_to_commit,
        message=f"docs(meetings): add Krisp transcript for {date} {slug} (auto-routed)",
    )

    graphiti_episode(
        body=(
            f"On {date}, project {cfg.display_name} had meeting "
            f"'{meeting.get('title')}' attended by "
            f"{', '.join(a.get('email') or a.get('name') or '?' for a in attendees)}. "
            f"Transcript filed at commit {sha[:8]}."
        ),
        name=f"{cfg.slug}-meeting-{date}-{slug}",
    )

    # Notify operators on each project's morning-standup recipients list.
    recipients = (cfg.raw.get("cadence", {})
                  .get("morning_standup", {})
                  .get("recipients", []))
    by_name = {s.name.lower(): s for s in cfg.stakeholders}
    for name in recipients:
        s = by_name.get(name.lower())
        if s and s.chat_id and s.chat_id != "TBD":
            telegram_send(
                s.chat_id,
                f"🎤 New meeting routed to {cfg.slug}: {meeting.get('title')} ({date})\n"
                f"  → {target_rel}",
            )

    return {"commit_sha": sha, "target_rel": target_rel}
```

(Note: `json` is already imported at the top of `_lib.py`; verify with `head -20 /Users/4c/AI/flyn-agent/deploy/pm/_lib.py`. If not, add `import json`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_route_meeting.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add deploy/pm/_lib.py deploy/pm/tests/
git commit -m "feat(pm): route_meeting_to_project() helper

Lifts the meeting-routing pattern from fathom_router.py (currently a
skeleton) into a reusable _lib helper. Writes transcript/notes/outline
into the project repo at docs/00-source/meetings/<date>_<slug>/, commits
+ pushes, ingests to Graphiti as an episode, and pings each project's
morning-standup recipients via Telegram.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — Categorizer

### Task 7: Rules classifier

**Files:**
- Create: `deploy/pm/meeting_classifier.py`
- Create: `deploy/pm/tests/test_classifier_rules.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/pm/tests/test_classifier_rules.py
"""Rules layer of the meeting classifier."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import ProjectConfig  # noqa: E402
from meeting_classifier import classify_by_rules  # noqa: E402


def _proj(slug: str, emails: list[str], keywords: list[str]) -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug,
        "repo": {"path": "/tmp/x", "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": f"S{i}", "role": "x", "side": "client",
             "primary_channel": "email", "email": e}
            for i, e in enumerate(emails)
        ],
        "meeting_keywords": keywords,
    })


def test_attendee_email_match():
    projects = [
        _proj("openliteracy", ["sarah@ol.org", "rebecca@ol.org"], []),
        _proj("cora", ["maria@cora.io"], []),
    ]
    meeting = {
        "title": "Quick chat",
        "attendees": [{"name": "S", "email": "sarah@ol.org"}],
    }
    slug, conf, reason = classify_by_rules(meeting, projects)
    assert slug == "openliteracy"
    assert conf == "rule"
    assert "sarah@ol.org" in reason


def test_title_keyword_match():
    projects = [
        _proj("openliteracy", [], ["OpenLiteracy", "OL Sprint"]),
        _proj("cora", [], ["Cora"]),
    ]
    meeting = {
        "title": "OL Sprint 1 mid-check",
        "attendees": [{"name": "Random", "email": "x@example.com"}],
    }
    slug, conf, reason = classify_by_rules(meeting, projects)
    assert slug == "openliteracy"
    assert "OL Sprint" in reason


def test_no_rule_match_returns_none():
    projects = [
        _proj("openliteracy", ["sarah@ol.org"], ["OpenLiteracy"]),
    ]
    meeting = {
        "title": "Lunch with mom",
        "attendees": [{"name": "Mom", "email": "mom@example.com"}],
    }
    slug, conf, reason = classify_by_rules(meeting, projects)
    assert slug is None
    assert conf == "no-rule"


def test_email_match_is_case_insensitive():
    projects = [_proj("openliteracy", ["sarah@OL.org"], [])]
    meeting = {"title": "x", "attendees": [{"email": "SARAH@ol.org"}]}
    slug, _, _ = classify_by_rules(meeting, projects)
    assert slug == "openliteracy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_classifier_rules.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Write meeting_classifier.py (rules only)**

```python
# deploy/pm/meeting_classifier.py
"""Classify a meeting against the registered projects.

Two layers, in order:
  1. Rules: attendee email match, or title-substring match against the
     project's meeting_keywords list.
  2. LLM: claude -p with a structured prompt. Returns project + confidence.

Both layers return (slug | None, confidence_label, reason). The caller
decides what to do with low-confidence LLM results.
"""

from __future__ import annotations

from typing import Iterable

from _lib import ProjectConfig


def _emails_for(cfg: ProjectConfig) -> set[str]:
    return {
        (s.email or "").lower()
        for s in cfg.stakeholders
        if s.email and s.email != "TBD"
    }


def _keywords_for(cfg: ProjectConfig) -> list[str]:
    kw = cfg.raw.get("meeting_keywords")
    if kw:
        return list(kw)
    # Fall back to fathom filter list if present (existing OL config).
    return list(cfg.raw.get("fathom", {}).get("filter_title_substrings", []))


def classify_by_rules(
    meeting: dict,
    projects: Iterable[ProjectConfig],
) -> tuple[str | None, str, str]:
    """Return (project_slug | None, confidence_label, reason).

    confidence_label is 'rule' on hit, 'no-rule' on miss.
    """
    attendee_emails = {
        (a.get("email") or "").lower()
        for a in (meeting.get("attendees") or [])
        if isinstance(a, dict)
    }
    title = (meeting.get("title") or "").lower()

    for cfg in projects:
        proj_emails = _emails_for(cfg)
        hit_email = next(iter(attendee_emails & proj_emails), None)
        if hit_email:
            return cfg.slug, "rule", f"attendee {hit_email} matches {cfg.slug}"
        for kw in _keywords_for(cfg):
            if kw.lower() in title:
                return cfg.slug, "rule", f"title contains '{kw}'"
    return None, "no-rule", "no project rule matched"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_classifier_rules.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add deploy/pm/meeting_classifier.py deploy/pm/tests/test_classifier_rules.py
git commit -m "feat(classifier): rules layer (attendee email + title keyword)

First of two layers. Reads each project's stakeholder emails and
meeting_keywords list (falls back to fathom.filter_title_substrings
for the existing OL config). Returns (slug, 'rule', reason) on hit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: LLM classifier (claude -p)

**Files:**
- Modify: `deploy/pm/meeting_classifier.py` (append)
- Create: `deploy/pm/prompts/meeting_classifier.md`
- Create: `deploy/pm/tests/test_classifier_llm.py`

- [ ] **Step 1: Create the prompt template**

```bash
mkdir -p /Users/4c/AI/flyn-agent/deploy/pm/prompts
```

```markdown
<!-- deploy/pm/prompts/meeting_classifier.md -->
You are Flyn's meeting categorizer. Given a meeting and a list of
projects, decide which project this meeting most likely belongs to.

**Output a single JSON object on the last line of your reply, no prose:**

```json
{"project": "<slug>" | null, "confidence": 0.0-1.0, "reason": "..."}
```

Be conservative. If the meeting could plausibly be personal, social, or
about a project not on the list, return `{"project": null, ...}` with low
confidence.

## Projects

{PROJECTS_BLOCK}

## Meeting

- **Title:** {TITLE}
- **Started:** {STARTED_AT}
- **Attendees:** {ATTENDEES}
- **Notes excerpt:**

{NOTES_EXCERPT}
```

- [ ] **Step 2: Write the failing test**

```python
# deploy/pm/tests/test_classifier_llm.py
"""LLM (claude -p) layer of the meeting classifier."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import ProjectConfig  # noqa: E402
from meeting_classifier import classify_by_llm  # noqa: E402


def _proj(slug: str) -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug.title(),
        "repo": {"path": "/tmp/x", "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": "S", "role": "x", "side": "client",
             "primary_channel": "email", "email": f"s@{slug}.example"},
        ],
    })


def _mock_claude(output: dict, returncode: int = 0):
    """Build a fake subprocess.run result."""
    class R:
        def __init__(self): self.returncode = returncode
        @property
        def stdout(self): return json.dumps({"result": json.dumps(output)})
        @property
        def stderr(self): return ""
    return R()


def test_llm_high_confidence_routes():
    projects = [_proj("openliteracy"), _proj("cora")]
    meeting = {"title": "Sync", "attendees": [], "notes_text": "OL pathways"}
    with patch("meeting_classifier.subprocess.run",
               return_value=_mock_claude(
                   {"project": "openliteracy", "confidence": 0.92,
                    "reason": "notes mention pathways"})):
        slug, conf, reason = classify_by_llm(meeting, projects)
    assert slug == "openliteracy"
    assert conf == "llm-high"


def test_llm_low_confidence_marks_low():
    projects = [_proj("openliteracy"), _proj("cora")]
    meeting = {"title": "Sync", "attendees": [], "notes_text": "..."}
    with patch("meeting_classifier.subprocess.run",
               return_value=_mock_claude(
                   {"project": "openliteracy", "confidence": 0.5,
                    "reason": "weak signal"})):
        slug, conf, _ = classify_by_llm(meeting, projects)
    assert slug == "openliteracy"
    assert conf == "llm-low"


def test_llm_null_project_returns_none():
    projects = [_proj("openliteracy")]
    meeting = {"title": "Brunch", "attendees": [], "notes_text": "..."}
    with patch("meeting_classifier.subprocess.run",
               return_value=_mock_claude(
                   {"project": None, "confidence": 0.1,
                    "reason": "looks personal"})):
        slug, _, _ = classify_by_llm(meeting, projects)
    assert slug is None


def test_llm_bad_json_falls_through():
    projects = [_proj("openliteracy")]
    meeting = {"title": "x", "attendees": [], "notes_text": ""}
    class R:
        returncode = 0
        stdout = '{"result": "not valid json {{"}'
        stderr = ""
    with patch("meeting_classifier.subprocess.run", return_value=R()):
        slug, conf, _ = classify_by_llm(meeting, projects)
    assert slug is None
    assert conf == "llm-error"


def test_llm_timeout_falls_through():
    import subprocess as sp
    projects = [_proj("openliteracy")]
    meeting = {"title": "x", "attendees": [], "notes_text": ""}
    def raise_timeout(*a, **kw):
        raise sp.TimeoutExpired(cmd="claude", timeout=60)
    with patch("meeting_classifier.subprocess.run", side_effect=raise_timeout):
        slug, conf, _ = classify_by_llm(meeting, projects)
    assert slug is None
    assert conf == "llm-error"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_classifier_llm.py -v`
Expected: FAIL with `ImportError: cannot import name 'classify_by_llm'`.

- [ ] **Step 4: Append classify_by_llm + helpers to meeting_classifier.py**

Add at the top of the file (with the existing imports):

```python
import json
import os
import subprocess
from pathlib import Path
```

Then append:

```python
PROMPT_PATH = Path(__file__).parent / "prompts" / "meeting_classifier.md"
CLAUDE_BIN = os.environ.get("FLYN_CLAUDE_P_BIN", "claude")
HIGH_CONFIDENCE_THRESHOLD = 0.8
LLM_TIMEOUT_SECONDS = 60


def _build_prompt(meeting: dict, projects: list[ProjectConfig]) -> str:
    template = PROMPT_PATH.read_text()
    projects_block = "\n".join(
        f"- **{p.slug}** — {p.display_name}\n  "
        f"Stakeholders: {', '.join(s.name for s in p.stakeholders)}"
        for p in projects
    )
    attendees = ", ".join(
        a.get("email") or a.get("name") or "?"
        for a in (meeting.get("attendees") or [])
    ) or "(none)"
    notes = (meeting.get("notes_text")
             or meeting.get("transcript_text") or "")[:2000]
    return (template
            .replace("{PROJECTS_BLOCK}", projects_block)
            .replace("{TITLE}", meeting.get("title") or "(untitled)")
            .replace("{STARTED_AT}", meeting.get("started_at") or "?")
            .replace("{ATTENDEES}", attendees)
            .replace("{NOTES_EXCERPT}", notes))


def _parse_llm_json(stdout: str) -> dict | None:
    """claude -p --output-format json wraps the assistant text in {"result": "..."}.
    The inner string should end with a JSON object on the last line."""
    try:
        outer = json.loads(stdout)
        inner = outer.get("result", "")
    except json.JSONDecodeError:
        inner = stdout
    # Find the last {...} block in inner.
    last_brace = inner.rfind("{")
    last_close = inner.rfind("}")
    if last_brace == -1 or last_close <= last_brace:
        return None
    try:
        return json.loads(inner[last_brace:last_close + 1])
    except json.JSONDecodeError:
        return None


def classify_by_llm(
    meeting: dict,
    projects: Iterable[ProjectConfig],
) -> tuple[str | None, str, str]:
    """Run claude -p to classify. Returns (slug | None, confidence_label, reason)."""
    projects = list(projects)
    prompt = _build_prompt(meeting, projects)
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True,
            timeout=LLM_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, "llm-error", "claude -p timed out"

    if result.returncode != 0:
        return None, "llm-error", f"claude -p exited {result.returncode}"

    parsed = _parse_llm_json(result.stdout)
    if not parsed:
        return None, "llm-error", "could not parse LLM JSON"

    slug = parsed.get("project")
    confidence = float(parsed.get("confidence") or 0.0)
    reason = parsed.get("reason") or ""

    if slug is None:
        return None, "llm-low", reason

    valid_slugs = {p.slug for p in projects}
    if slug not in valid_slugs:
        return None, "llm-error", f"LLM returned unknown slug '{slug}'"

    label = "llm-high" if confidence >= HIGH_CONFIDENCE_THRESHOLD else "llm-low"
    return slug, label, f"confidence={confidence:.2f}; {reason}"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_classifier_llm.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add deploy/pm/meeting_classifier.py deploy/pm/prompts/meeting_classifier.md deploy/pm/tests/test_classifier_llm.py
git commit -m "feat(classifier): claude -p LLM fallback layer

Runs claude -p with a structured prompt, parses last-JSON-block from
the response (defensively), and returns llm-high/llm-low/llm-error
labels. Timeout 60s; bad JSON or non-zero exit falls through to caller.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Categorizer main module + executable

**Files:**
- Create: `deploy/pm/meeting_categorizer.py`
- Create: `deploy/pm/tests/test_categorizer_main.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/pm/tests/test_categorizer_main.py
"""End-to-end test of the nightly categorizer main loop (with mocks)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Set DB env BEFORE importing modules that read it.
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "wiki-backend"))

from _lib import ProjectConfig  # noqa: E402
import meetings_db  # noqa: E402
import meeting_categorizer as mcat  # noqa: E402


def _seed_meeting(conn, meeting_id: str, title: str, attendees: list):
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, attendees, status) "
        "VALUES (?, ?, ?, 'pending')",
        (meeting_id, title, json.dumps(attendees)),
    )


def _proj(slug: str, emails: list[str]) -> ProjectConfig:
    return ProjectConfig(slug=slug, raw={
        "display_name": slug,
        "repo": {"path": "/tmp/repo", "remote": "origin", "branch": "main"},
        "stakeholders": [
            {"name": f"S{i}", "role": "x", "side": "client",
             "primary_channel": "email", "email": e}
            for i, e in enumerate(emails)
        ],
    })


@pytest.fixture
def fresh_db():
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.init_db()
    return meetings_db._connect()


def test_rule_match_routes_meeting(fresh_db):
    _seed_meeting(fresh_db, "mtg-r1", "x",
                  [{"email": "sarah@ol.org"}])
    projects = [_proj("openliteracy", ["sarah@ol.org"])]

    with patch.object(mcat, "list_projects_for_classifier",
                      return_value=projects), \
         patch.object(mcat, "route_meeting_to_project",
                      return_value={"commit_sha": "abc", "target_rel": "x"}) as r:
        mcat.run_once()

    r.assert_called_once()
    row = fresh_db.execute(
        "SELECT status, routed_project FROM meetings WHERE meeting_id='mtg-r1'"
    ).fetchone()
    assert row[0] == "routed"
    assert row[1] == "openliteracy"


def test_unmatched_meeting_becomes_review(fresh_db):
    _seed_meeting(fresh_db, "mtg-u1", "Lunch",
                  [{"email": "mom@example.com"}])
    projects = [_proj("openliteracy", ["sarah@ol.org"])]

    with patch.object(mcat, "list_projects_for_classifier",
                      return_value=projects), \
         patch.object(mcat, "classify_by_llm",
                      return_value=(None, "llm-low", "weak signal")), \
         patch.object(mcat, "route_meeting_to_project") as r:
        mcat.run_once()

    r.assert_not_called()
    row = fresh_db.execute(
        "SELECT status FROM meetings WHERE meeting_id='mtg-u1'"
    ).fetchone()
    assert row[0] == "review"


def test_routing_failure_marks_error(fresh_db):
    _seed_meeting(fresh_db, "mtg-e1", "x",
                  [{"email": "sarah@ol.org"}])
    projects = [_proj("openliteracy", ["sarah@ol.org"])]

    with patch.object(mcat, "list_projects_for_classifier",
                      return_value=projects), \
         patch.object(mcat, "route_meeting_to_project",
                      side_effect=RuntimeError("git push failed")):
        mcat.run_once()

    row = fresh_db.execute(
        "SELECT status FROM meetings WHERE meeting_id='mtg-e1'"
    ).fetchone()
    assert row[0] == "error"


def test_stuck_classifying_rows_revert(fresh_db):
    fresh_db.execute(
        "INSERT INTO meetings (meeting_id, title, attendees, status, updated_at) "
        "VALUES (?, ?, '[]', 'classifying', datetime('now', '-2 hours'))",
        ("mtg-stuck", "x"),
    )
    with patch.object(mcat, "list_projects_for_classifier", return_value=[]), \
         patch.object(mcat, "classify_by_llm",
                      return_value=(None, "no-rule", "")):
        mcat.unstick_old_classifying()
    row = fresh_db.execute(
        "SELECT status FROM meetings WHERE meeting_id='mtg-stuck'"
    ).fetchone()
    assert row[0] == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_categorizer_main.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write meeting_categorizer.py**

```python
#!/usr/bin/env python3
"""Nightly meeting categorizer.

For each meeting with status='pending':
  1. Try rules (attendee/title).
  2. Fall back to claude -p.
  3. If a project is matched with sufficient confidence, route it
     (write into repo, push, ingest, ping). Mark 'routed'.
  4. Otherwise mark 'review' for the morning digest to surface.

Also un-sticks rows left in 'classifying' >1h from a previous crash.

Usage:
  python3 meeting_categorizer.py            # one pass
  python3 meeting_categorizer.py --noop     # classify but don't route or write
  python3 meeting_categorizer.py --unstick  # only revert stuck rows
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wiki-backend"))

from _lib import (  # noqa: E402
    ProjectConfig,
    PROJECTS_ROOT,
    load_project,
    route_meeting_to_project,
)
from meeting_classifier import classify_by_rules, classify_by_llm  # noqa: E402
import meetings_db  # noqa: E402


def list_projects_for_classifier() -> list[ProjectConfig]:
    """Read every project config under ~/.openclaw/projects/."""
    out = []
    if not PROJECTS_ROOT.exists():
        return out
    for sub in sorted(PROJECTS_ROOT.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "config.yaml").exists():
            continue
        try:
            out.append(load_project(sub.name))
        except Exception as e:  # noqa: BLE001
            print(f"[categorizer] skipping {sub.name}: {e}", file=sys.stderr)
    return out


def unstick_old_classifying() -> int:
    """Revert rows stuck in 'classifying' >1h."""
    meetings_db.init_db()
    conn = meetings_db._connect()
    try:
        cur = conn.execute(
            "UPDATE meetings SET status='pending', "
            "updated_at=datetime('now') "
            "WHERE status='classifying' AND "
            "(julianday('now') - julianday(updated_at)) * 24 >= 1"
        )
        return cur.rowcount or 0
    finally:
        conn.close()


def _meeting_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["attendees"] = json.loads(d.get("attendees") or "[]")
    except json.JSONDecodeError:
        d["attendees"] = []
    return d


def run_once(noop: bool = False) -> dict[str, int]:
    """One categorizer pass. Returns counts keyed by outcome."""
    meetings_db.init_db()
    counts = {"routed": 0, "review": 0, "error": 0, "skipped": 0}
    projects = list_projects_for_classifier()
    conn = meetings_db._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM meetings WHERE status='pending'"
        ).fetchall()
        for row in rows:
            meeting = _meeting_row_to_dict(row)
            mid = meeting["meeting_id"]
            conn.execute(
                "UPDATE meetings SET status='classifying', "
                "updated_at=datetime('now') WHERE meeting_id=?",
                (mid,),
            )

            slug, conf, reason = classify_by_rules(meeting, projects)
            if not slug:
                slug, conf, reason = classify_by_llm(meeting, projects)

            if slug and conf in ("rule", "llm-high"):
                if noop:
                    new_status, counts_key = "pending", "skipped"
                    conn.execute(
                        "UPDATE meetings SET status=?, classifier_reason=?, "
                        "classifier_confidence=?, updated_at=datetime('now') "
                        "WHERE meeting_id=?",
                        (new_status, reason, conf, mid),
                    )
                    counts[counts_key] += 1
                    continue
                try:
                    cfg = next(p for p in projects if p.slug == slug)
                    res = route_meeting_to_project(meeting, cfg)
                    conn.execute(
                        "UPDATE meetings SET status='routed', "
                        "routed_project=?, routed_commit_sha=?, "
                        "classifier_reason=?, classifier_confidence=?, "
                        "routed_at=datetime('now'), updated_at=datetime('now') "
                        "WHERE meeting_id=?",
                        (slug, res["commit_sha"], reason, conf, mid),
                    )
                    meetings_db.audit(
                        conn, actor="categorizer", meeting_id=mid,
                        action="routed",
                        payload=json.dumps({"project": slug, "sha": res["commit_sha"]}),
                    )
                    counts["routed"] += 1
                except Exception as e:  # noqa: BLE001
                    conn.execute(
                        "UPDATE meetings SET status='error', "
                        "classifier_reason=?, updated_at=datetime('now') "
                        "WHERE meeting_id=?",
                        (f"route failed: {e}", mid),
                    )
                    meetings_db.audit(
                        conn, actor="categorizer", meeting_id=mid,
                        action="route_failed",
                        payload=json.dumps({"error": str(e)}),
                    )
                    counts["error"] += 1
            else:
                conn.execute(
                    "UPDATE meetings SET status='review', "
                    "classifier_reason=?, classifier_confidence=?, "
                    "updated_at=datetime('now') WHERE meeting_id=?",
                    (reason, conf, mid),
                )
                meetings_db.audit(
                    conn, actor="categorizer", meeting_id=mid,
                    action="marked_review",
                    payload=json.dumps({"reason": reason, "confidence": conf}),
                )
                counts["review"] += 1
    finally:
        conn.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noop", action="store_true",
                    help="Classify but don't route or change state")
    ap.add_argument("--unstick", action="store_true",
                    help="Only revert stuck 'classifying' rows, then exit")
    args = ap.parse_args()

    if args.unstick:
        n = unstick_old_classifying()
        print(f"unstuck {n} row(s)")
        return 0

    unstick_old_classifying()
    counts = run_once(noop=args.noop)
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_categorizer_main.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add deploy/pm/meeting_categorizer.py deploy/pm/tests/test_categorizer_main.py
git commit -m "feat(categorizer): nightly main loop + unstick old classifying rows

Reads pending meetings, runs rules-then-LLM classifier, routes
confident matches via route_meeting_to_project, marks the rest
as 'review' for the morning digest. --noop mode classifies without
side effects. --unstick reverts rows stuck >1h.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Cron Wiring

### Task 10: launchd plist + shell wrapper

**Files:**
- Create: `deploy/cron/scripts/meeting-categorize.sh`
- Create: `deploy/launchd/ai.flyn.pulse.meeting-categorize.plist`

- [ ] **Step 1: Write the shell wrapper**

```bash
# deploy/cron/scripts/meeting-categorize.sh
#!/usr/bin/env bash
# Pulse: meeting-categorize
# Runs nightly at 02:30 to route pending Krisp meetings.

PULSE_NAME="meeting-categorize"
source "$(dirname "$0")/common.sh"

log "start"

PY=/Users/4c/AI/flyn-agent/deploy/wiki-backend/.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi

cd /Users/4c/AI/flyn-agent/deploy/pm
OUTPUT="$("$PY" meeting_categorizer.py 2>&1)" || {
  log "categorizer exited non-zero: $OUTPUT"
  alert_telegram "categorizer failed: ${OUTPUT:0:200}"
  exit 1
}
log "result: $OUTPUT"

# If any meetings are now in 'review', ping #flyn-briefing with the count.
REVIEW_COUNT="$(sqlite3 ~/.openclaw/data/flyn-meetings.db \
  "SELECT COUNT(*) FROM meetings WHERE status='review'" 2>/dev/null || echo 0)"
if [ "${REVIEW_COUNT:-0}" -gt 0 ]; then
  openclaw channels send --channel telegram --target '#flyn-briefing' \
    --message "🎤 ${REVIEW_COUNT} meeting(s) need routing — see morning digest for /route commands." \
    >/dev/null 2>&1 || log "channel send failed"
fi

log "done"
```

Make it executable:

```bash
chmod +x /Users/4c/AI/flyn-agent/deploy/cron/scripts/meeting-categorize.sh
```

- [ ] **Step 2: Write the plist**

```xml
<!-- deploy/launchd/ai.flyn.pulse.meeting-categorize.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.flyn.pulse.meeting-categorize</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>/Users/4c/AI/flyn-agent/deploy/cron/scripts/meeting-categorize.sh</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>/Users/4c</string>
    <key>PATH</key><string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>2</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
  <key>StandardOutPath</key><string>/Users/4c/.openclaw/logs/cron-ai.flyn.pulse.meeting-categorize.log</string>
  <key>StandardErrorPath</key><string>/Users/4c/.openclaw/logs/cron-ai.flyn.pulse.meeting-categorize.err</string>
</dict></plist>
```

- [ ] **Step 3: Install + load (do this manually, not in a CI/auto step)**

```bash
cp /Users/4c/AI/flyn-agent/deploy/launchd/ai.flyn.pulse.meeting-categorize.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.flyn.pulse.meeting-categorize.plist
launchctl list | grep meeting-categorize
```

Expected last line: a PID (or `-` with exit 0) and the label.

- [ ] **Step 4: Smoke-run by hand**

```bash
bash /Users/4c/AI/flyn-agent/deploy/cron/scripts/meeting-categorize.sh
tail -20 ~/.openclaw/logs/heartbeat-meeting-categorize-$(date +%Y-%m-%d).log
```

Expected: JSON counts (e.g., `{"routed": 0, "review": 0, "error": 0, "skipped": 0}`) when there are no pending meetings.

- [ ] **Step 5: Commit**

```bash
git add deploy/cron/scripts/meeting-categorize.sh deploy/launchd/ai.flyn.pulse.meeting-categorize.plist
git commit -m "feat(cron): nightly meeting-categorize at 02:30

Wraps meeting_categorizer.py with the shared common.sh logging +
Telegram alerts. Pings #flyn-briefing if any meetings land in
'review' status so the morning digest reader knows to triage.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6 — Morning Digest + /route Command

### Task 11: Extend morning_standup.py with review meetings section

**Files:**
- Modify: `deploy/pm/morning_standup.py`
- Create: `deploy/pm/tests/test_morning_review_section.py`

- [ ] **Step 1: Read the existing morning_standup.py to find the right insertion point**

```bash
sed -n '1,60p' /Users/4c/AI/flyn-agent/deploy/pm/morning_standup.py
grep -n "def \|telegram_send\|return\|main\|print(" /Users/4c/AI/flyn-agent/deploy/pm/morning_standup.py
```

Locate the function that assembles the digest body (likely a function that returns a string, called from `main`).

- [ ] **Step 2: Write the failing test**

```python
# deploy/pm/tests/test_morning_review_section.py
"""Test the new review-meetings section appended to the morning digest."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "wiki-backend"))

import meetings_db  # noqa: E402
import morning_standup as ms  # noqa: E402


@pytest.fixture
def seeded_db():
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.init_db()
    conn = meetings_db._connect()
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, started_at, attendees, "
        "status, classifier_reason) VALUES "
        "(?, ?, ?, ?, 'review', ?)",
        ("m1", "Sync w/ Jen", "2026-05-14T15:00:00Z",
         json.dumps([{"email": "jen@example.com"}]),
         "no rule matched, llm-low"),
    )
    conn.close()
    return _tmpdb.name


def test_review_section_lists_meetings(seeded_db, tmp_path):
    state_file = tmp_path / "last-review-list.json"
    section = ms.build_review_meetings_section(state_path=state_file)
    assert "1." in section
    assert "Sync w/ Jen" in section
    assert "/route 1 " in section  # at least one /route hint present
    assert state_file.exists()
    saved = json.loads(state_file.read_text())
    assert saved[0]["meeting_id"] == "m1"


def test_review_section_empty_returns_empty_string(tmp_path):
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.init_db()
    state_file = tmp_path / "last-review-list.json"
    section = ms.build_review_meetings_section(state_path=state_file)
    assert section == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_morning_review_section.py -v`
Expected: FAIL (function doesn't exist).

- [ ] **Step 4: Add build_review_meetings_section to morning_standup.py**

Append near the bottom of `deploy/pm/morning_standup.py` (above `main()` or wherever utility functions live):

```python
import json as _json_for_review  # local alias if json import is shadowed
from pathlib import Path as _PathForReview


def _list_known_project_slugs() -> list[str]:
    """Best-effort: enumerate ~/.openclaw/projects/*/config.yaml."""
    root = _PathForReview.home() / ".openclaw" / "projects"
    if not root.exists():
        return []
    return sorted(
        sub.name for sub in root.iterdir()
        if sub.is_dir() and (sub / "config.yaml").exists()
    )


def build_review_meetings_section(state_path: _PathForReview | None = None) -> str:
    """Query flyn-meetings.db for meetings in 'review' and render a
    Telegram-friendly section with /route hints. Writes a JSON
    state file so the /route command can resolve list indexes."""
    import sys as _sys
    _sys.path.insert(0, str(_PathForReview(__file__).resolve().parent.parent / "wiki-backend"))
    import meetings_db as _mdb

    _mdb.init_db()
    conn = _mdb._connect()
    try:
        rows = conn.execute(
            "SELECT meeting_id, title, started_at, duration_seconds, "
            "attendees, classifier_reason FROM meetings "
            "WHERE status='review' ORDER BY started_at DESC NULLS LAST"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return ""

    state_path = state_path or (
        _PathForReview.home() / ".openclaw" / "state" / "last-review-list.json"
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)

    slugs = _list_known_project_slugs()
    lines = [f"🎤 *Unclassified meetings ({len(rows)})*", ""]
    saved: list[dict] = []
    for i, row in enumerate(rows, start=1):
        attendees = []
        try:
            attendees = _json_for_review.loads(row["attendees"] or "[]")
        except Exception:  # noqa: BLE001
            pass
        n_att = len(attendees)
        dur_min = (row["duration_seconds"] or 0) // 60
        when = (row["started_at"] or "?")[:16].replace("T", " ")
        lines.append(
            f"{i}. {when} — \"{row['title'] or '(no title)'}\" "
            f"({dur_min}m, {n_att} attendees)"
        )
        for slug in slugs:
            lines.append(f"   /route {i} {slug}")
        lines.append(f"   /route {i} skip")
        lines.append("")
        saved.append({"index": i, "meeting_id": row["meeting_id"]})

    state_path.write_text(_json_for_review.dumps(saved, indent=2))
    return "\n".join(lines).rstrip() + "\n"
```

Then find the existing function that builds the digest body (look for the place sections are concatenated) and append the result of `build_review_meetings_section()` to the body. Example (adapt to actual structure):

```python
# Inside the digest assembly function:
sections.append(build_review_meetings_section())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_morning_review_section.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add deploy/pm/morning_standup.py deploy/pm/tests/test_morning_review_section.py
git commit -m "feat(digest): unclassified-meetings section with /route hints

Queries flyn-meetings.db for status='review' rows, renders them with
numbered /route <i> <project> hints (one per known project + skip),
and persists the index→meeting_id map to ~/.openclaw/state/
last-review-list.json so the /route command handler can resolve
indexes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: /route command handler

**Files:**
- Create: `deploy/pm/route_command.py`
- Create: `deploy/pm/tests/test_route_command.py`

This task implements the command parser + DB updater as a Python entrypoint. Wiring it into the openclaw gateway's Telegram command-dispatch is the *next* step, but the parser/handler module is independently testable now.

- [ ] **Step 1: Write the failing test**

```python
# deploy/pm/tests/test_route_command.py
"""Test the /route command handler."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmpdb.close()
os.environ["FLYN_MEETINGS_DB"] = _tmpdb.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "wiki-backend"))

import meetings_db  # noqa: E402
import route_command  # noqa: E402


@pytest.fixture
def setup(tmp_path):
    meetings_db._initialized = False
    Path(_tmpdb.name).unlink(missing_ok=True)
    meetings_db.init_db()
    conn = meetings_db._connect()
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, attendees, status) "
        "VALUES (?, ?, '[]', 'review')",
        ("m-route-1", "x"),
    )
    conn.close()
    state = tmp_path / "last-review-list.json"
    state.write_text(json.dumps([{"index": 1, "meeting_id": "m-route-1"}]))
    return state


def test_route_skip_marks_dropped(setup):
    res = route_command.handle("/route 1 skip", state_path=setup)
    assert res["ok"] is True
    conn = meetings_db._connect()
    status = conn.execute(
        "SELECT status FROM meetings WHERE meeting_id='m-route-1'"
    ).fetchone()[0]
    conn.close()
    assert status == "dropped"


def test_route_to_project_calls_router(setup):
    with patch("route_command.load_project") as lp, \
         patch("route_command.route_meeting_to_project",
               return_value={"commit_sha": "deadbeef", "target_rel": "x"}) as rm:
        lp.return_value = type("C", (), {"slug": "openliteracy"})()
        res = route_command.handle("/route 1 openliteracy", state_path=setup)
    assert res["ok"] is True
    assert "deadbeef" in res["reply"]
    rm.assert_called_once()


def test_unknown_index_errors(setup):
    res = route_command.handle("/route 99 openliteracy", state_path=setup)
    assert res["ok"] is False
    assert "index" in res["reply"].lower()


def test_bad_usage_errors(setup):
    res = route_command.handle("/route", state_path=setup)
    assert res["ok"] is False


def test_force_reroute_blocked_by_default(setup):
    # Pre-mark as routed
    conn = meetings_db._connect()
    conn.execute("UPDATE meetings SET status='routed' WHERE meeting_id='m-route-1'")
    conn.close()
    res = route_command.handle("/route 1 openliteracy", state_path=setup)
    assert res["ok"] is False
    assert "already" in res["reply"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_route_command.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write route_command.py**

```python
#!/usr/bin/env python3
"""Handler for the Telegram `/route <index> <project|skip>` command.

Parses the command, looks up the meeting_id in the morning-digest's
state file, and routes (or marks dropped). Returns a dict the gateway
can use to reply.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wiki-backend"))

from _lib import load_project, route_meeting_to_project  # noqa: E402
import meetings_db  # noqa: E402


DEFAULT_STATE = Path.home() / ".openclaw" / "state" / "last-review-list.json"


def _load_state(state_path: Path) -> list[dict]:
    if not state_path.exists():
        return []
    try:
        return json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return []


def _meeting_id_for_index(state: list[dict], idx: int) -> str | None:
    for row in state:
        if row.get("index") == idx:
            return row.get("meeting_id")
    return None


def handle(message: str, state_path: Path | None = None) -> dict:
    """Parse and execute. Returns {ok: bool, reply: str}."""
    state_path = state_path or DEFAULT_STATE
    parts = message.strip().split()
    if len(parts) < 3 or parts[0] != "/route":
        return {"ok": False,
                "reply": "Usage: /route <index> <project-slug | skip>"}
    try:
        idx = int(parts[1])
    except ValueError:
        return {"ok": False, "reply": f"Index must be a number, got '{parts[1]}'"}
    target = parts[2].lower()

    state = _load_state(state_path)
    meeting_id = _meeting_id_for_index(state, idx)
    if not meeting_id:
        return {"ok": False,
                "reply": f"No meeting at index {idx} in today's review list."}

    meetings_db.init_db()
    conn = meetings_db._connect()
    try:
        row = conn.execute(
            "SELECT * FROM meetings WHERE meeting_id=?", (meeting_id,)
        ).fetchone()
        if not row:
            return {"ok": False,
                    "reply": f"Meeting {meeting_id} no longer in DB."}
        if row["status"] in ("routed", "dropped"):
            return {"ok": False,
                    "reply": f"Meeting {meeting_id} is already "
                             f"{row['status']}; no-op."}

        if target == "skip":
            conn.execute(
                "UPDATE meetings SET status='dropped', "
                "classifier_reason='manual skip', "
                "updated_at=datetime('now') WHERE meeting_id=?",
                (meeting_id,),
            )
            meetings_db.audit(
                conn, actor="route-cmd", meeting_id=meeting_id,
                action="dropped",
                payload=json.dumps({"index": idx}),
            )
            return {"ok": True, "reply": f"Meeting {idx} ({row['title']}) dropped."}

        try:
            cfg = load_project(target)
        except FileNotFoundError:
            return {"ok": False, "reply": f"Unknown project '{target}'."}

        meeting = dict(row)
        try:
            meeting["attendees"] = json.loads(meeting.get("attendees") or "[]")
        except json.JSONDecodeError:
            meeting["attendees"] = []

        try:
            res = route_meeting_to_project(meeting, cfg)
        except Exception as e:  # noqa: BLE001
            return {"ok": False,
                    "reply": f"Routing failed: {type(e).__name__}: {e}"}

        conn.execute(
            "UPDATE meetings SET status='routed', routed_project=?, "
            "routed_commit_sha=?, routed_at=datetime('now'), "
            "updated_at=datetime('now') WHERE meeting_id=?",
            (cfg.slug, res["commit_sha"], meeting_id),
        )
        meetings_db.audit(
            conn, actor="route-cmd", meeting_id=meeting_id,
            action="routed",
            payload=json.dumps({"project": cfg.slug, "sha": res["commit_sha"]}),
        )
        return {"ok": True,
                "reply": f"Routed to {cfg.slug} @ {res['commit_sha'][:8]}"}
    finally:
        conn.close()


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("message", help="The /route command text")
    args = ap.parse_args()
    out = handle(args.message)
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/4c/AI/flyn-agent/deploy/pm && python3 -m pytest tests/test_route_command.py -v`
Expected: 5 passed.

- [ ] **Step 5: Manually wire the command into the gateway**

The openclaw gateway forwards `/`-prefixed messages from authorized chat_ids to a per-agent command. Inspect:

```bash
ls /Users/4c/.openclaw/agents/main/commands/ 2>/dev/null
cat /Users/4c/.openclaw/agents/main/agent.yaml 2>/dev/null | head -40
```

If there's an existing pattern (e.g., commands are shell scripts that get invoked with the message body on stdin), add a script:

```bash
cat > /Users/4c/.openclaw/agents/main/commands/route.sh <<'EOF'
#!/usr/bin/env bash
# /route command handler — calls flyn's route_command.py
exec /Users/4c/AI/flyn-agent/deploy/wiki-backend/.venv/bin/python \
  /Users/4c/AI/flyn-agent/deploy/pm/route_command.py "$1"
EOF
chmod +x /Users/4c/.openclaw/agents/main/commands/route.sh
```

If the convention is different (Python plugin, registered handler in agent.yaml, etc.), match it. The python module is the contract; the gateway wiring is the integration.

Smoke-test from CLI:

```bash
/Users/4c/AI/flyn-agent/deploy/wiki-backend/.venv/bin/python \
  /Users/4c/AI/flyn-agent/deploy/pm/route_command.py "/route 1 openliteracy"
```

Expected: JSON `{"ok": false, "reply": "No meeting at index 1..."}` (if no review list yet).

- [ ] **Step 6: Commit**

```bash
git add deploy/pm/route_command.py deploy/pm/tests/test_route_command.py
git commit -m "feat(route): /route <index> <project|skip> command handler

Parses Telegram /route messages, resolves the index against the
morning digest state file, and either calls route_meeting_to_project
or marks the meeting dropped. Refuses to re-route already-routed
meetings (no --force in v1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 7 — Operability

### Task 13: Smoke script + sample fixture

**Files:**
- Create: `scripts/dev/krisp_smoke.sh`
- Create: `scripts/dev/fixtures/krisp_sample.json`
- Create: `scripts/dev/inspect_payloads.py`

- [ ] **Step 1: Create the directory + sample fixture**

```bash
mkdir -p /Users/4c/AI/flyn-agent/scripts/dev/fixtures
```

```json
{
  "event_id": "smoke-2026-05-14-001",
  "event_type": "transcript.created",
  "meeting": {
    "id": "mtg-smoke-001",
    "title": "Smoke test from fixture",
    "url": "https://krisp.ai/m/smoke",
    "started_at": "2026-05-14T20:00:00Z",
    "ended_at": "2026-05-14T20:25:00Z",
    "duration_seconds": 1500,
    "attendees": [
      {"name": "Ryan Shuken", "email": "ryanshuken@gmail.com"},
      {"name": "Beth Kukla", "email": "beth@example.com"}
    ]
  },
  "transcript": {
    "text": "Ryan: Hey Beth, quick check on the OL sprint.\nBeth: Sounds good."
  }
}
```

Save as `scripts/dev/fixtures/krisp_sample.json`.

- [ ] **Step 2: Create the smoke script**

```bash
# scripts/dev/krisp_smoke.sh
#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${ENDPOINT:-http://127.0.0.1:8200/api/meetings/krisp}"
TOKEN="${FLYN_KRISP_TOKEN:?set FLYN_KRISP_TOKEN to your shared secret}"
FIXTURE="${1:-$(dirname "$0")/fixtures/krisp_sample.json}"

echo "→ POST $ENDPOINT  (fixture: $FIXTURE)"
RESP="$(curl -sS -w '\n---HTTP %{http_code}---\n' -X POST "$ENDPOINT" \
  -H "X-OL-Krisp-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  --data @"$FIXTURE")"
echo "$RESP"

echo "→ Inspecting DB"
sqlite3 "${FLYN_MEETINGS_DB:-$HOME/.openclaw/data/flyn-meetings.db}" -header -column \
  "SELECT meeting_id, title, status, classifier_confidence FROM meetings ORDER BY first_seen_at DESC LIMIT 5"
```

```bash
chmod +x /Users/4c/AI/flyn-agent/scripts/dev/krisp_smoke.sh
```

- [ ] **Step 3: Create the payload inspector**

```python
#!/usr/bin/env python3
# scripts/dev/inspect_payloads.py
"""Pretty-print the last N raw payloads from meeting_events.

Useful during the first week: real Krisp payloads land here and we
adjust krisp_webhook._extract_meeting_fields() against what we actually
see.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    args = ap.parse_args()
    db = Path(os.environ.get(
        "FLYN_MEETINGS_DB",
        str(Path.home() / ".openclaw" / "data" / "flyn-meetings.db"),
    ))
    if not db.exists():
        print(f"no db at {db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_id, received_at, event_type, meeting_id, raw_payload "
        "FROM meeting_events ORDER BY id DESC LIMIT ?",
        (args.n,),
    ).fetchall()
    conn.close()
    for r in rows:
        print(f"=== {r['received_at']}  event_id={r['event_id']}  "
              f"type={r['event_type']}  meeting_id={r['meeting_id']} ===")
        try:
            print(json.dumps(json.loads(r["raw_payload"]), indent=2))
        except json.JSONDecodeError:
            print(r["raw_payload"])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```bash
chmod +x /Users/4c/AI/flyn-agent/scripts/dev/inspect_payloads.py
```

- [ ] **Step 4: Run the full smoke loop**

```bash
# 1. Reload the wiki-backend with the new env var
TOKEN_VALUE="$(openssl rand -hex 32)"
echo "$TOKEN_VALUE"   # remember this — needed for Krisp config + smoke script
# Append to ~/.openclaw/openclaw.json under a new krisp.webhookToken key
# Update ~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist to export it
# Reload:
launchctl unload ~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist
launchctl load   ~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist
sleep 2
curl -sS http://127.0.0.1:8200/api/health

# 2. Run the smoke
FLYN_KRISP_TOKEN="$TOKEN_VALUE" /Users/4c/AI/flyn-agent/scripts/dev/krisp_smoke.sh

# 3. Inspect what landed
/Users/4c/AI/flyn-agent/scripts/dev/inspect_payloads.py -n 3
```

Expected: smoke script prints `received: true`; the inspector shows the fixture payload.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev/
git commit -m "test(krisp): smoke script + sample fixture + payload inspector

krisp_smoke.sh POSTs the fixture to the live endpoint and queries the
DB; inspect_payloads.py pretty-prints recent raw payloads so we can
adapt _extract_meeting_fields() to real Krisp shapes during week one.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Disaster-recovery doc updates

**Files:**
- Modify: `DISASTER-RECOVERY.md`
- Modify: `RESUME-HERE.md`

- [ ] **Step 1: Find existing sections in DISASTER-RECOVERY.md to mirror**

```bash
grep -n "^## \|^### " /Users/4c/AI/flyn-agent/DISASTER-RECOVERY.md
```

- [ ] **Step 2: Append a "Meeting pipeline" section to DISASTER-RECOVERY.md**

Add a new section near similar service sections. Content:

```markdown
## Meeting pipeline (Krisp webhook + nightly categorizer)

**Services in the pipeline:**
- `ai.flyn.ol-wiki-backend` (existing) — hosts `POST /api/meetings/krisp`
- `ai.flyn.pulse.meeting-categorize` (new) — nightly 02:30

**State:**
- `~/.openclaw/data/flyn-meetings.db` — meeting_events, meetings, meeting_audit
- `~/.openclaw/state/last-review-list.json` — morning digest's index→meeting_id map
- `~/.openclaw/openclaw.json` `krisp.webhookToken` — shared secret with Krisp

### Common failures and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `POST /api/meetings/krisp` returns 401 | `FLYN_KRISP_TOKEN` env var not loaded into the wiki-backend launchd job | Edit `~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist` `EnvironmentVariables` dict; `launchctl unload && load` |
| Krisp dashboard shows webhook errors | Tailscale Funnel down, or wiki-backend not running | `tailscale funnel status`; `launchctl list \| grep ol-wiki-backend` |
| Categorizer never routes anything | `claude` not on PATH for the launchd context, or no project rules match | Run `bash deploy/cron/scripts/meeting-categorize.sh` by hand and read the log; check `which claude` |
| Meeting stuck in 'classifying' | Categorizer crashed mid-loop | Auto-revert kicks in next run; or run `python3 deploy/pm/meeting_categorizer.py --unstick` |
| Telegram `/route N skip` errors with "no meeting at index N" | Stale state file (older than today's digest) | Re-run morning digest to refresh `~/.openclaw/state/last-review-list.json` |
| Wrong project routing decision | Rules too loose, or LLM hallucinated | Manual `git revert` of the meeting commit; mark DB row `status='dropped'`; tighten rules in project config |

### Disaster: full restore

1. Restore `flyn-meetings.db` from the nightly backup pulse (`~/Backups/flyn/`).
2. `cp deploy/launchd/ai.flyn.pulse.meeting-categorize.plist ~/Library/LaunchAgents/ && launchctl load ...`
3. Re-enter `krisp.webhookToken` into `~/.openclaw/openclaw.json` (rotate by editing Krisp's webhook config to match).
4. Reload wiki-backend.
```

- [ ] **Step 3: Update RESUME-HERE.md**

Add a new entry under "Live state — verify everything is up":

```bash
# 9. Meeting pipeline
sqlite3 ~/.openclaw/data/flyn-meetings.db "SELECT status, COUNT(*) FROM meetings GROUP BY status"
launchctl list | grep meeting-categorize
```

Add to "Live surfaces" table:

```markdown
| Krisp webhook endpoint | https://4cs-mac-mini.tailc7d8af.ts.net/api/meetings/krisp |
| Meeting inbox DB | `~/.openclaw/data/flyn-meetings.db` |
```

Replace "In-flight / next actions" item #2 (Pearl Platform video) with a parenthetical that the Krisp pipeline supersedes the manual-transcribe approach for *new* meetings; Pearl remains a one-off manual job for the existing 27MB recording.

- [ ] **Step 4: Commit**

```bash
git add DISASTER-RECOVERY.md RESUME-HERE.md
git commit -m "docs(ops): meeting pipeline disaster recovery + RESUME-HERE updates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final smoke + Krisp configuration

(Not a coded task — these are the operator steps to turn the pipeline on.)

1. **Generate + save the token**
   ```bash
   TOKEN="$(openssl rand -hex 32)"
   echo "$TOKEN"  # copy this
   # Edit ~/.openclaw/openclaw.json — add: "krisp": {"webhookToken": "<token>"}
   ```

2. **Inject into the wiki-backend launchd job**
   Edit `~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist` to add inside `EnvironmentVariables`:
   ```xml
   <key>FLYN_KRISP_TOKEN</key><string>...the same token...</string>
   ```
   Then:
   ```bash
   launchctl unload ~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist
   launchctl load ~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist
   ```

3. **Configure Krisp**
   - Krisp app → Settings → Integrations → Webhook → Connect
   - Name: `flyn-mac-mini`
   - URL: `https://4cs-mac-mini.tailc7d8af.ts.net/api/meetings/krisp`
   - Triggers: all five (Transcript Created, Meeting Note Generated, Outline Generated, Key Points Generated, Transcript Shared)
   - Custom header: `X-OL-Krisp-Token: <token>`

4. **Send Krisp's "Test Event"** (or record a 60-second meeting). Verify:
   ```bash
   /Users/4c/AI/flyn-agent/scripts/dev/inspect_payloads.py -n 1
   ```

5. **First-week protocol**
   Run `meeting_categorizer.py --noop` until you've seen 2-3 real meetings classify correctly without surprises. Then let the nightly cron handle it.

---

## Self-review

**Spec coverage:**
- ✓ Webhook receiver (Tasks 3-5)
- ✓ Meeting inbox DB (Task 1)
- ✓ Pydantic models (Task 2)
- ✓ Routing helper (Task 6)
- ✓ Rules classifier (Task 7)
- ✓ LLM classifier (Task 8)
- ✓ Categorizer main loop + unstick (Task 9)
- ✓ Cron + plist (Task 10)
- ✓ Morning digest extension (Task 11)
- ✓ /route command (Task 12)
- ✓ Smoke + inspector (Task 13)
- ✓ DR docs + RESUME-HERE (Task 14)
- ✓ Krisp configuration steps (final section)

**Placeholder scan:** no TBDs, no "implement later", no "similar to Task N" without code.

**Type consistency:** `meeting_id` is the join key across DB / `_extract_meeting_fields` / `route_meeting_to_project` / `meeting_classifier` / `route_command`. `confidence_label` values are consistent: `rule`, `no-rule`, `llm-high`, `llm-low`, `llm-error`. Status values: `pending`, `classifying`, `routed`, `review`, `dropped`, `error` — used consistently across modules.

**One ambiguity called out for implementer:** Task 5 step 4 says "search for `init_db()` in `app.py`" because the exact line for adding `meetings_db.init_db()` depends on whether the existing init is lifespan-based or at module load. The implementer follows the actual pattern, not a prescribed line number.
