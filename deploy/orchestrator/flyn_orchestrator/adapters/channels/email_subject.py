"""email_subject.py — Subject-line tagging convention for Flyn email channel.

Tags appear in square brackets at the start of the subject:
  [FLYN-TASK] Start the redesign
  [FLYN-REPLY:T-0042] Re: Start the redesign
  [FLYN-APPROVE:T-0042] Approved
  [FLYN-REJECT:T-0042] Changes needed

See docs/email-subject-tags.md for the full convention.
"""
from __future__ import annotations
import re
from typing import Optional

# Canonical tag strings
TAG_TASK = "FLYN-TASK"
TAG_REPLY = "FLYN-REPLY"
TAG_APPROVE = "FLYN-APPROVE"
TAG_REJECT = "FLYN-REJECT"

# All recognised tags (for validation / routing)
KNOWN_TAGS = frozenset({TAG_TASK, TAG_REPLY, TAG_APPROVE, TAG_REJECT})

_SUBJECT_RE = re.compile(r"\s*\[([A-Z-]+)(?::([A-Z0-9_-]+))?\]\s*(.*)", re.DOTALL)


def parse_subject(subject: str) -> dict:
    """Parse a Flyn-tagged subject line.

    Returns a dict with:
      tag           — e.g. 'FLYN-REPLY' or None if no tag
      task_id       — e.g. 'T-0042' or None
      clean_subject — the rest of the subject after the tag prefix
    """
    if not subject:
        return {"tag": None, "task_id": None, "clean_subject": ""}

    m = _SUBJECT_RE.match(subject)
    if m:
        return {
            "tag": m.group(1),
            "task_id": m.group(2),       # None if the :TASKID part is absent
            "clean_subject": m.group(3) or "",
        }

    return {"tag": None, "task_id": None, "clean_subject": subject}


def format_subject(tag: str, task_id: Optional[str], body: str) -> str:
    """Build a Flyn-tagged subject line.

    Examples:
      format_subject(TAG_TASK, None, "Start the redesign")
        → "[FLYN-TASK] Start the redesign"
      format_subject(TAG_REPLY, "T-0042", "Re: Start the redesign")
        → "[FLYN-REPLY:T-0042] Re: Start the redesign"
    """
    prefix = f"[{tag}:{task_id}]" if task_id else f"[{tag}]"
    return f"{prefix} {body}"
