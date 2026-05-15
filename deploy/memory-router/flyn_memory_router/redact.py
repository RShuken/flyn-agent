"""Secret-redactor library. Called on every outbound payload."""
from __future__ import annotations

import re
from typing import Optional

REDACTED_PREFIX = "[REDACTED:"


# Order matters: more specific patterns first. Each tuple = (pattern, class).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic-key"),
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "openai-key"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "github-pat"),
    (re.compile(r"gho_[A-Za-z0-9]{36}"), "github-oauth"),
    (re.compile(r"glpat-[A-Za-z0-9_-]{20}"), "gitlab-pat"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"), "bearer"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "slack"),
    (
        re.compile(
            r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_/+=-]{16,}"
        ),
        "credential",
    ),
    (re.compile(r"~/\.ssh/[^\s]+"), "ssh-path"),
    (re.compile(r"~/\.aws/credentials[^\s]*"), "aws-path"),
    (re.compile(r"~/\.openclaw/agents/[^\s]+"), "openclaw-secret-path"),
]


def redact(s: Optional[str]) -> str:
    """Replace credential-like patterns with `[REDACTED:<class>]`. Fails closed on `None`."""
    if not s:
        return ""
    out = s
    for pattern, klass in _PATTERNS:
        out = pattern.sub(f"{REDACTED_PREFIX}{klass}]", out)
    return out


def _redact_value(v):
    """Recursively redact a single value of arbitrary type."""
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: _redact_value(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return [_redact_value(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_redact_value(x) for x in v)
    return v


def redact_dict(d: dict) -> dict:
    """Recursively redact all string values in a dict (including those inside
    nested dicts, lists, and tuples). Non-string scalars pass through unchanged.
    Returns a new dict; input is not mutated."""
    return {k: _redact_value(v) for k, v in d.items()}
