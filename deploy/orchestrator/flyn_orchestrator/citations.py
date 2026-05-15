"""Citation extraction + validation for the research workflow.

Researchers emit JSON with `citations: [{url, title, claim, accessed_at}, ...]`.
This module parses + validates that shape, surfaces problems for the critic to
re-evaluate, and provides a Citation dataclass for the synthesizer.

Public surface (importable by other modules):
  Citation            — frozen dataclass for a single source reference
  ResearcherOutput    — frozen dataclass for a researcher's full response
  _extract_json_block — finds a JSON object inside free text (fenced or bare);
                        used by research.py (Task 3) via `from .citations import _extract_json_block`
  parse_researcher_output — parse raw researcher text → ResearcherOutput | None
  validate_citations  — check a list of Citations → list[str] of findings
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Citation:
    url: str
    title: str
    claim: str
    accessed_at: str


@dataclass(frozen=True)
class ResearcherOutput:
    sub_question_id: str
    sub_question: str
    answer: str
    citations: list[Citation]
    confidence: str
    open_questions: list[str]


# Suspicious URL shorteners — citations to these should be flagged
_SHORTENER_DOMAINS = {
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly",
    "buff.ly", "is.gd", "tr.im", "v.gd", "x.co",
}


def _extract_json_block(text: str) -> Optional[str]:
    """Find a JSON object in *text*.  Handles fenced (```json ... ```) and bare.

    This function is intentionally module-level accessible (not truly private
    despite the leading underscore) so that research.py can import it:
        from .citations import _extract_json_block
    """
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        return fenced.group(1)
    # Bare object: greedy match from first { to last }
    if "{" in text and "}" in text:
        start = text.find("{")
        # Walk to find the balanced closing brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def parse_researcher_output(raw: str) -> Optional[ResearcherOutput]:
    """Parse the researcher's JSON output.  Returns None on any malformed input."""
    if not raw or not raw.strip():
        return None
    block = _extract_json_block(raw)
    if not block:
        return None
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return None
    # Validate required fields
    required = {"sub_question_id", "sub_question", "answer", "citations", "confidence"}
    if not required.issubset(d.keys()):
        return None
    if not isinstance(d.get("citations"), list):
        return None
    try:
        cites = [
            Citation(
                url=str(c.get("url", "")),
                title=str(c.get("title", "")),
                claim=str(c.get("claim", "")),
                accessed_at=str(c.get("accessed_at", "")),
            )
            for c in d["citations"]
        ]
    except (AttributeError, TypeError):
        return None
    return ResearcherOutput(
        sub_question_id=str(d["sub_question_id"]),
        sub_question=str(d["sub_question"]),
        answer=str(d["answer"]),
        citations=cites,
        confidence=str(d["confidence"]),
        open_questions=list(d.get("open_questions") or []),
    )


def validate_citations(citations: list[Citation]) -> list[str]:
    """Return a list of human-readable findings (problems).  Empty list = clean.

    Checks:
    - URL must look like a real http(s)://... URL
    - URL host must not be in the shortener allowlist
    - accessed_at must be non-empty (YYYY-MM-DD format ideally)
    - No duplicate URLs within the same citation list
    """
    findings: list[str] = []
    seen: dict[str, int] = {}
    for i, c in enumerate(citations):
        if not c.url:
            findings.append(f"citation {i}: missing URL")
            continue
        if not re.match(r"^https?://[^\s]+\.[^\s]{2,}", c.url):
            findings.append(f"citation {i}: URL doesn't look real: {c.url!r}")
            continue
        # Check for shorteners
        m = re.match(r"^https?://([^/]+)", c.url)
        if m:
            host = m.group(1).lower()
            for shortener in _SHORTENER_DOMAINS:
                if host == shortener or host.endswith("." + shortener):
                    findings.append(
                        f"citation {i}: URL uses a shortener ({c.url}); "
                        "replace with the resolved canonical URL"
                    )
                    break
        if not c.accessed_at:
            findings.append(f"citation {i}: missing accessed_at date")
        # Track duplicates
        if c.url in seen:
            findings.append(
                f"citation {i}: duplicate URL (also at index {seen[c.url]}): {c.url}"
            )
        else:
            seen[c.url] = i
    return findings
