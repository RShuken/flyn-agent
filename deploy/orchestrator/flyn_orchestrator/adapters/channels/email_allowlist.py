"""Load the EmailChannelAdapter's trusted-sender allowlist from CONTACTS.md.

Replaces the hardcoded `DEFAULT_ALLOWLIST` constant in `email.py` per the
§Δ.6-partial threat ("allowlist hardcoded vs CONTACTS.md").

The loader looks for a section heading::

    ## Email allowlist (Flyn EmailChannelAdapter)

(or any heading whose text contains "Email allowlist", case-insensitive)
followed by markdown bullets. Each bullet's email-shaped text is added to the
allowlist. Lines inside HTML comment blocks (`<!-- ... -->`) are ignored, and
lines without an `@` character are skipped (catches `TBD` placeholders).

Returns ``None`` when the file is missing OR contains no parseable allowlist
section, so callers can fall back to a hardcoded default.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional


_HEADING_RE = re.compile(r"^\s*#{2,}\s+.*email\s*allowlist", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(r"^\s*#{1,3}\s")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def load_allowlist_from_contacts(contacts_path: Path) -> Optional[frozenset[str]]:
    """Return the email allowlist parsed from CONTACTS.md, or None.

    None means "no allowlist section was found" — caller should fall back to
    a hardcoded default. An empty (but present) section returns ``frozenset()``,
    meaning "explicit reject-everything" — caller honors it.
    """
    try:
        text = contacts_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    return _parse_allowlist(text)


def _parse_allowlist(text: str) -> Optional[frozenset[str]]:
    """Core parser. Separated for unit-test ergonomics."""
    lines = text.splitlines()

    # Find the email-allowlist heading
    start_idx = None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            start_idx = i + 1
            break
    if start_idx is None:
        return None

    # Collect bullet emails until we hit the next heading (## or below) or EOF
    emails: set[str] = set()
    in_html_comment = False
    for line in lines[start_idx:]:
        stripped = line.strip()
        # Detect HTML comment block boundaries
        if "<!--" in stripped:
            in_html_comment = True
        if "-->" in stripped:
            in_html_comment = False
            continue
        if in_html_comment:
            continue
        # Stop at next heading
        if _NEXT_HEADING_RE.match(line):
            break
        # Extract bullet content
        m = _BULLET_RE.match(line)
        if not m:
            continue
        content = m.group(1)
        # Skip if no email shape (e.g., "TBD")
        em = _EMAIL_RE.search(content)
        if em:
            emails.add(em.group(1).lower())

    # Section present but empty → explicit empty allowlist
    return frozenset(emails)
