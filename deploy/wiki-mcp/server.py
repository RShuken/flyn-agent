#!/usr/bin/env python3
"""OL Project-Management Wiki MCP server.

Exposes the FastAPI backend (running on 127.0.0.1:8200) as MCP tools so:
  - Flyn (via openclaw MCP client) can list/answer/reassign questions and
    create decisions
  - Claude Code (Ryan/Eric/Beth) can do the same via `claude mcp add`

Design:
  - Thin HTTP client. All auth + audit lives in the API. This server is
    just a tool surface.
  - Reads skip auth. Writes load the API key from auth-profiles.json at
    startup and inject it as X-API-Key.
  - stdio transport (FastMCP default) — works with both openclaw + Claude
    Code MCP clients.

Env overrides:
  OL_WIKI_API_BASE   default http://127.0.0.1:8200 (local on 4C)
  OL_WIKI_API_KEY    pulled from auth-profiles.json if unset
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

API_BASE = os.environ.get("OL_WIKI_API_BASE", "http://127.0.0.1:8200")
AUTH_PROFILES = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"


def _load_api_key() -> str:
    """Load API key from env override, then auth-profiles.json. Returns '' if not found."""
    if v := os.environ.get("OL_WIKI_API_KEY"):
        return v
    try:
        d = json.loads(AUTH_PROFILES.read_text())
        return d["profiles"]["ol_wiki_api:default"]["token"]
    except Exception:
        return ""


API_KEY = _load_api_key()
mcp = FastMCP(
    "ol-wiki",
    instructions=(
        "OpenLiteracy Phase 2 project-management wiki. "
        "Source of truth for the 124+ open questions across 15 sections, the "
        "decisions log, and the audit trail. Reads are open; writes pass through "
        "an authenticated API. Use list_questions to filter by owner/status/sprint, "
        "answer_question when an OL stakeholder provides an answer, and "
        "create_decision when something is settled (matches RESOLVED.md entries)."
    ),
)


def _http() -> httpx.Client:
    return httpx.Client(base_url=API_BASE, timeout=10.0)


# -------------------- Read tools --------------------

@mcp.tool
def list_questions(
    owner: str | None = None,
    status: str | None = None,
    bucket: str | None = None,
    section: str | None = None,
    target_sprint: int | None = None,
    q: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List project questions with optional filters.

    Args:
        owner: full name (e.g. "Rebecca Patterson"). Use stats() to see counts per owner.
        status: "open" | "pending-answer" | "answered" | "deferred"
        bucket: "ai-does" | "ai-generates" | "ai-assists" | "human-only" | "bucket-unclear" | "Conflict"
        section: single letter A-N or P
        target_sprint: 1, 2, or 3
        q: free-text search across question text / ask / id
        limit: max rows (default 100, max 2000)

    Returns a list of question dicts with id, section, text, ask, bucket,
    owner, status, depends_on, target_sprint, answered_at, answered_by, answer_text.
    """
    params: dict[str, Any] = {"limit": limit}
    for k, v in {"owner": owner, "status": status, "bucket": bucket,
                 "section": section, "target_sprint": target_sprint, "q": q}.items():
        if v is not None:
            params[k] = v
    with _http() as c:
        r = c.get("/api/questions", params=params)
        r.raise_for_status()
        return r.json()


@mcp.tool
def get_question(question_id: str) -> dict[str, Any]:
    """Get one question by id (e.g. "A.5", "N.1", "P.7").

    Returns the full question record including answer_text if it has been
    answered.
    """
    with _http() as c:
        r = c.get(f"/api/questions/{question_id}")
        if r.status_code == 404:
            return {"error": f"question {question_id} not found"}
        r.raise_for_status()
        return r.json()


@mcp.tool
def list_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """List decisions made (most recent first).

    Each decision records: decided_at, decided_by, summary (one line),
    body_md (full rationale + verbatim quotes), the question_ids it
    resolves, and source_meeting if applicable.
    """
    with _http() as c:
        r = c.get("/api/decisions", params={"limit": limit})
        r.raise_for_status()
        return r.json()


@mcp.tool
def stats() -> dict[str, Any]:
    """Aggregate project stats: total questions, by_status, by_owner,
    by_sprint, by_bucket, decisions count, last audit timestamp.

    Use to answer questions like "how many questions are still open for
    Rebecca?", "what's still blocking sprint 1?", or "how many decisions
    has the team made so far?"
    """
    with _http() as c:
        r = c.get("/api/stats")
        r.raise_for_status()
        return r.json()


# -------------------- Write tools (authenticated) --------------------

def _auth_headers() -> dict[str, str]:
    if not API_KEY:
        raise RuntimeError("No API key configured; cannot perform writes")
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@mcp.tool
def answer_question(
    question_id: str,
    answer_text: str,
    answered_by: str,
) -> dict[str, Any]:
    """Mark a question as answered with the provided answer.

    Use this when an OL stakeholder (Sarah / Rebecca / Greta) replies to a
    question by email, message, or in a meeting. answer_text should be the
    actual answer as concise as the source allows. answered_by is who
    provided the answer (e.g. "Rebecca Patterson").

    The mutation is audit-logged and the question's status flips to
    'answered'. Once answered, the wiki reflects it on next page load.
    """
    with _http() as c:
        r = c.post(
            f"/api/questions/{question_id}/answer",
            headers=_auth_headers(),
            json={"answer_text": answer_text, "answered_by": answered_by},
        )
        if r.status_code == 404:
            return {"error": f"question {question_id} not found"}
        r.raise_for_status()
        return r.json()


@mcp.tool
def reassign_question(
    question_id: str,
    new_owner: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Reassign question ownership to a different stakeholder.

    Use when a question is initially owned by one person but really
    requires another to answer (e.g., a question routed to Rebecca that's
    actually a Greta UI question). The reassignment is audit-logged with
    the optional reason.
    """
    with _http() as c:
        r = c.post(
            f"/api/questions/{question_id}/reassign",
            headers=_auth_headers(),
            json={"owner": new_owner, "reason": reason},
        )
        if r.status_code == 404:
            return {"error": f"question {question_id} not found"}
        r.raise_for_status()
        return r.json()


@mcp.tool
def create_decision(
    decided_by: str,
    summary: str,
    body_md: str,
    question_ids: list[str] | None = None,
    source_meeting: str | None = None,
) -> dict[str, Any]:
    """Record a decision that the team / client made.

    Args:
        decided_by: who made the call (e.g., "Sarah Scott Frank", or
            "Sarah Scott Frank + Eric Schneider" for joint decisions)
        summary: one-line headline (e.g., "QR-code → teacher phone, OPTIONAL")
        body_md: full rationale in markdown — include verbatim quotes from
            the source (meeting transcript, email, message) when possible
        question_ids: list of question ids this decision resolves (those
            questions don't automatically flip to 'answered'; call
            answer_question separately when appropriate)
        source_meeting: e.g., "2026-05-11_sprint1-kickoff" (matches the
            folder name in docs/00-source/meetings/)

    Returns the created decision with its id.
    """
    with _http() as c:
        r = c.post(
            "/api/decisions",
            headers=_auth_headers(),
            json={
                "decided_by": decided_by,
                "summary": summary,
                "body_md": body_md,
                "question_ids": question_ids or [],
                "source_meeting": source_meeting,
            },
        )
        r.raise_for_status()
        return r.json()


@mcp.tool
def list_audit(limit: int = 25) -> list[dict[str, Any]]:
    """List recent mutation events (most recent first). Requires API key.

    Each entry: ts, actor, action, payload. Useful for "what changed in
    the project today" or "who answered what this week".
    """
    with _http() as c:
        r = c.get("/api/audit", headers=_auth_headers(), params={"limit": limit})
        r.raise_for_status()
        return r.json()


# -------------------- entrypoint --------------------

if __name__ == "__main__":
    mcp.run()
