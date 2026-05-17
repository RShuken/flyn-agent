"""email_auth.py — SPF/DKIM verification via Authentication-Results header (RFC 8601).

Real mail servers (Gmail, Cloudflare Email Routing, Postmark) prepend this
header with the receiving server's auth verdict before forwarding. We parse
it to gate inbound email acceptance.
"""
from __future__ import annotations
import re
from typing import Optional


def parse_authentication_results(headers: dict) -> dict:
    """Parse Authentication-Results header (RFC 8601).

    Returns {'spf': 'pass'|'fail'|'unknown', 'dkim': 'pass'|'fail'|'unknown', 'raw': str}.
    Handles both canonical and lowercase header names.
    """
    raw = headers.get("Authentication-Results", "") or headers.get("authentication-results", "")
    if not raw:
        return {"spf": "unknown", "dkim": "unknown", "raw": ""}

    spf: str = "unknown"
    dkim: str = "unknown"

    m_spf = re.search(r"spf\s*=\s*(\w+)", raw, re.IGNORECASE)
    if m_spf:
        spf = m_spf.group(1).lower()

    m_dkim = re.search(r"dkim\s*=\s*(\w+)", raw, re.IGNORECASE)
    if m_dkim:
        dkim = m_dkim.group(1).lower()

    return {"spf": spf, "dkim": dkim, "raw": raw}


def verify_email_auth(
    headers: dict,
    sender_email: str,
    *,
    allowlist: Optional[frozenset] = None,
) -> tuple[bool, str]:
    """Decide whether an inbound email is allowed.

    Rules:
    - Allowlisted sender always passes, regardless of auth headers.
    - Otherwise BOTH spf AND dkim must NOT explicitly fail, AND at least one
      must explicitly pass. Missing headers (both unknown) → reject.

    Returns (allow: bool, reason: str).
    """
    allowlist = allowlist or frozenset()

    if sender_email.lower() in (a.lower() for a in allowlist):
        return True, "allowlist"

    auth = parse_authentication_results(headers)

    if auth["spf"] == "fail" or auth["dkim"] == "fail":
        return False, f"auth failed: spf={auth['spf']}, dkim={auth['dkim']}"

    if auth["spf"] == "unknown" and auth["dkim"] == "unknown":
        return False, "no auth headers"

    return True, f"auth ok: spf={auth['spf']}, dkim={auth['dkim']}"
