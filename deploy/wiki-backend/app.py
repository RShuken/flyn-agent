"""OL Project-Management Wiki Backend.

FastAPI app for the OpenLiteracy Phase 2 master-plan wiki. Provides:
  - Read endpoints for questions, decisions, audit, stats (open)
  - Write endpoints for answering questions, reassigning owners, creating
    decisions (bearer-auth)
  - Health + meta

Auth: bearer token in `X-API-Key` header. Token loaded from env
OL_WIKI_API_KEY at startup. Missing token = writes refused.

Design choices:
  - Source of truth for question content stays the markdown registry. The
    DB only carries STATE on top of it (status, answer, audit, decisions).
  - Reads are open so the static-deployed wiki on Cloudflare Pages can
    fetch from anywhere without a token.
  - Mutations require auth + are audit-logged + announce themselves via a
    pluggable event sink (no-op default; future: Telegram pings to Beth).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from db import audit, get_conn, init_db
from models import (
    AnswerQuestion,
    AuditEntry,
    Decision,
    Health,
    NewDecision,
    Question,
    ReassignQuestion,
    Stats,
)


API_KEY = os.environ.get("OL_WIKI_API_KEY", "")
PROJECT_SLUG = os.environ.get("OL_WIKI_PROJECT", "openliteracy")


# -------------------- lifecycle --------------------

app = FastAPI(
    title="OL Project-Management Wiki API",
    version="0.1.0",
    description="Backend for the OpenLiteracy Phase 2 master-plan wiki.",
)

# CORS: the wiki on Cloudflare Pages must be allowed to fetch.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ol-explainer-wiki.pages.dev",
        "https://*.ol-explainer-wiki.pages.dev",
        "http://localhost:8765",
        "http://127.0.0.1:8765",
        "http://4cs-mac-mini:8765",
    ],
    allow_origin_regex=r"https://.*\.ol-explainer-wiki\.pages\.dev",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# -------------------- auth --------------------

def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> str:
    if not API_KEY:
        # API key not configured — fail closed
        raise HTTPException(status_code=503, detail="API not configured for writes")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return x_api_key


# -------------------- helpers --------------------

def _row_to_question(row: sqlite3.Row) -> Question:
    d = dict(row)
    d["depends_on"] = json.loads(d.get("depends_on") or "[]")
    return Question(**d)


def _row_to_decision(row: sqlite3.Row) -> Decision:
    d = dict(row)
    d["question_ids"] = json.loads(d.get("question_ids") or "[]")
    return Decision(**d)


def _row_to_audit(row: sqlite3.Row) -> AuditEntry:
    d = dict(row)
    d["payload"] = json.loads(d.get("payload") or "{}")
    return AuditEntry(**d)


# -------------------- read endpoints (open) --------------------

@app.get("/api/health", response_model=Health, tags=["meta"])
def health(conn: sqlite3.Connection = Depends(get_conn)) -> Health:
    n = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    return Health(status="ok", db="sqlite", questions_count=n)


@app.get("/api/questions", response_model=list[Question], tags=["questions"])
def list_questions(
    conn: sqlite3.Connection = Depends(get_conn),
    owner: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    bucket: str | None = Query(None),
    section: str | None = Query(None),
    target_sprint: int | None = Query(None),
    q: str | None = Query(None, description="Free-text search over text + ask"),
    limit: int = Query(500, le=2000),
) -> list[Question]:
    sql = "SELECT * FROM questions WHERE 1=1"
    params: list = []
    if owner:
        sql += " AND owner = ?"; params.append(owner)
    if status_filter:
        sql += " AND status = ?"; params.append(status_filter)
    if bucket:
        sql += " AND bucket = ?"; params.append(bucket)
    if section:
        sql += " AND section = ?"; params.append(section)
    if target_sprint is not None:
        sql += " AND target_sprint = ?"; params.append(target_sprint)
    if q:
        sql += " AND (text LIKE ? OR ask LIKE ? OR id LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY section, id LIMIT ?"
    params.append(limit)
    return [_row_to_question(r) for r in conn.execute(sql, params).fetchall()]


@app.get("/api/questions/{question_id}", response_model=Question, tags=["questions"])
def get_question(question_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> Question:
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Question {question_id} not found")
    return _row_to_question(row)


@app.get("/api/decisions", response_model=list[Decision], tags=["decisions"])
def list_decisions(
    conn: sqlite3.Connection = Depends(get_conn),
    limit: int = Query(200, le=1000),
) -> list[Decision]:
    rows = conn.execute(
        "SELECT * FROM decisions ORDER BY decided_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_decision(r) for r in rows]


@app.get("/api/stats", response_model=Stats, tags=["meta"])
def stats(conn: sqlite3.Connection = Depends(get_conn)) -> Stats:
    total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]

    def grouped(col: str) -> dict[str, int]:
        rows = conn.execute(f"SELECT {col}, COUNT(*) FROM questions GROUP BY {col}").fetchall()
        return {(r[0] if r[0] is not None else "none"): r[1] for r in rows}

    sprints = {"1": 0, "2": 0, "3": 0, "none": 0}
    for r in conn.execute(
        "SELECT target_sprint, COUNT(*) FROM questions GROUP BY target_sprint"
    ).fetchall():
        key = str(r[0]) if r[0] is not None else "none"
        sprints[key] = r[1]

    decisions_total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    last_audit_row = conn.execute("SELECT ts FROM audit_log ORDER BY ts DESC LIMIT 1").fetchone()
    last_audit = datetime.fromisoformat(last_audit_row[0]) if last_audit_row else None

    return Stats(
        questions_total=total,
        by_status=grouped("status"),
        by_owner=grouped("owner"),
        by_sprint=sprints,
        by_bucket=grouped("bucket"),
        decisions_total=decisions_total,
        last_audit_at=last_audit,
    )


# -------------------- write endpoints (bearer auth) --------------------

@app.post("/api/questions/{question_id}/answer", response_model=Question, tags=["questions"])
def answer_question(
    question_id: str,
    payload: AnswerQuestion,
    _key: str = Depends(require_api_key),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Question:
    row = conn.execute("SELECT id, status FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Question {question_id} not found")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE questions
           SET status = 'answered',
               answered_at = ?,
               answered_by = ?,
               answer_text = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (now, payload.answered_by, payload.answer_text, now, question_id),
    )
    audit(conn, actor=payload.answered_by, action="question.answered",
          payload={"question_id": question_id, "answer_text": payload.answer_text})

    new_row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    return _row_to_question(new_row)


@app.post("/api/questions/{question_id}/reassign", response_model=Question, tags=["questions"])
def reassign_question(
    question_id: str,
    payload: ReassignQuestion,
    _key: str = Depends(require_api_key),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Question:
    row = conn.execute("SELECT id, owner FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Question {question_id} not found")
    old_owner = row["owner"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE questions SET owner = ?, updated_at = ? WHERE id = ?",
        (payload.owner, now, question_id),
    )
    audit(conn, actor="api", action="question.reassigned",
          payload={"question_id": question_id, "from": old_owner, "to": payload.owner,
                   "reason": payload.reason})
    new_row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    return _row_to_question(new_row)


@app.post("/api/decisions", response_model=Decision, status_code=status.HTTP_201_CREATED, tags=["decisions"])
def create_decision(
    payload: NewDecision,
    _key: str = Depends(require_api_key),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Decision:
    cur = conn.execute(
        """
        INSERT INTO decisions (decided_by, summary, body_md, question_ids, source_meeting)
        VALUES (?, ?, ?, ?, ?)
        """,
        (payload.decided_by, payload.summary, payload.body_md,
         json.dumps(payload.question_ids), payload.source_meeting),
    )
    new_id = cur.lastrowid
    audit(conn, actor=payload.decided_by, action="decision.created",
          payload={"decision_id": new_id, "summary": payload.summary,
                   "question_ids": payload.question_ids})
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (new_id,)).fetchone()
    return _row_to_decision(row)


@app.get("/api/audit", response_model=list[AuditEntry], tags=["meta"])
def list_audit(
    _key: str = Depends(require_api_key),
    conn: sqlite3.Connection = Depends(get_conn),
    limit: int = Query(100, le=500),
) -> list[AuditEntry]:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_audit(r) for r in rows]
