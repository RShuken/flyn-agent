"""Classify a meeting against the registered projects.

Two layers, in order:
  1. Rules: attendee email match, or title-substring match against the
     project's meeting_keywords list.
  2. LLM: claude -p with a structured prompt. Returns project + confidence.

Both layers return (slug | None, confidence_label, reason). The caller
decides what to do with low-confidence LLM results.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
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
