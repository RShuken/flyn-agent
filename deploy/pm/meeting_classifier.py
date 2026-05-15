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
