"""injection_detect.py — Pattern-based prompt-injection detector for inbound email bodies.

Runs on every inbound email body before the message is passed to the
orchestrator. Returns (suspicious: bool, reasons: list[str]).

Best-effort: never raises. An empty or None body is not suspicious.
"""
from __future__ import annotations
import re
from typing import Optional

# (regex_pattern, label) pairs.  Order matters for readability; all are
# searched with re.IGNORECASE.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all|previous|prior|the above)\s+instructions", "instruction-override"),
    (r"disregard\s+(all|previous|prior|the above)", "instruction-override"),
    (r"forget\s+(all|previous|everything)", "instruction-override"),
    (r"you\s+are\s+now\s+", "role-reassignment"),
    (r"system\s+prompt", "system-prompt-reference"),
    (r"new\s+instructions\s*:", "instruction-injection"),
    (r"</user>|<system>|<assistant>", "role-confusion-tag"),
    (r"BEGIN\s+PROMPT|END\s+PROMPT", "prompt-boundary-injection"),
]

# Unicode zero-width / BOM characters commonly used to hide injected content
ZERO_WIDTH_CHARS: list[str] = [
    "​",  # ZERO WIDTH SPACE
    "‌",  # ZERO WIDTH NON-JOINER
    "‍",  # ZERO WIDTH JOINER
    "﻿",  # ZERO WIDTH NO-BREAK SPACE (BOM)
]

# Minimum length of a Base64-looking blob to flag it (shorter strings appear
# in normal email text, e.g. quoted attachments or short tokens).
_BASE64_MIN_LEN = 200

# Minimum consecutive whitespace characters to flag as suspicious padding
_WHITESPACE_MIN_LEN = 50


def detect_injection(body: Optional[str]) -> tuple[bool, list[str]]:
    """Scan *body* for prompt-injection indicators.

    Returns (suspicious: bool, reasons: list[str]).  The reasons list is empty
    when suspicious is False.  Each reason is a short slug string (no spaces)
    suitable for logging or structured output.

    Never raises.
    """
    if not body:
        return False, []

    reasons: list[str] = []

    # 1. Pattern-based checks
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, body, re.IGNORECASE):
            if label not in reasons:
                reasons.append(label)

    # 2. Zero-width / invisible characters
    for ch in ZERO_WIDTH_CHARS:
        if ch in body:
            reasons.append("zero-width-unicode")
            break  # one flag is enough; don't double-count

    # 3. Long Base64-looking blob (data exfiltration or hidden payload)
    if re.search(rf"[A-Za-z0-9+/=]{{{_BASE64_MIN_LEN},}}", body):
        reasons.append("base64-blob")

    # 4. Excessive whitespace padding (used to push injected content below
    #    the visible fold)
    if re.search(rf"[\s\n]{{{_WHITESPACE_MIN_LEN},}}", body):
        reasons.append("excessive-whitespace")

    return (bool(reasons), reasons)
