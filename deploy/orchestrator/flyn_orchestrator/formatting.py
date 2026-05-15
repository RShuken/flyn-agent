"""Per-platform output formatting for the content workflow.

Inputs: a markdown-ish draft + a platform name.
Outputs: a FormattedOutput with .text (formatted) and .warnings (e.g. length).

Platform handlers are simple — Phase 4 MVP uses passthrough or minimal
massaging. Phase 4b can add Slack mrkdwn, full HTML email templates, etc.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Literal


Platform = Literal[
    "telegram", "email", "slack", "plain", "markdown",
    "tweet", "linkedin", "generic",
]

# Type alias for warning strings — exposed so other modules can annotate.
PlatformWarning = str

MAX_LENGTHS: dict[str, int] = {
    "tweet": 280,
    "linkedin": 3000,
}


@dataclass(frozen=True)
class FormattedOutput:
    text: str
    warnings: list[str] = field(default_factory=list)


def _strip_html(s: str) -> str:
    """Drop HTML tags. Naive — fine for Phase 4 MVP."""
    return re.sub(r"<[^>]+>", "", s).strip()


def _strip_markdown_emphasis(s: str) -> str:
    """Remove **bold**, *italic*, `code`, [link](url) markdown emphasis."""
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s


def _wrap_email(body: str) -> str:
    """Minimal HTML email wrapper. Preserves paragraphs; converts **bold** to <strong>."""
    html_body = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", body)
    html_body = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", html_body)
    paras = html_body.split("\n\n")
    para_html = "\n".join(f"<p>{p.strip()}</p>" for p in paras if p.strip())
    return (
        "<html><body style=\"font-family: -apple-system, system-ui, sans-serif;\">\n"
        f"{para_html}\n"
        "</body></html>"
    )


def format_for_platform(draft: str, *, platform: str) -> FormattedOutput:
    """Massage a draft for a target platform. Returns FormattedOutput."""
    if not draft:
        return FormattedOutput(text="", warnings=[])

    warnings: list[str] = []
    p = platform.lower().strip() if platform else "generic"

    if p == "telegram":
        text = _strip_html(draft)
        if len(text) > 4096:
            warnings.append(f"Telegram message length ({len(text)}) exceeds 4096-char limit")
    elif p == "email":
        text = _wrap_email(draft)
    elif p == "slack":
        # Slack mrkdwn: *bold* (single asterisk), _italic_, `code` — convert from
        # Markdown **bold** to *bold*
        text = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", draft)
    elif p == "plain":
        text = _strip_markdown_emphasis(draft)
    elif p == "tweet":
        text = draft.strip()
        if len(text) > MAX_LENGTHS["tweet"]:
            warnings.append(
                f"Tweet is {len(text)} chars, over 280 limit — consider trimming or threading"
            )
    elif p == "linkedin":
        text = draft.strip()
        if len(text) > MAX_LENGTHS["linkedin"]:
            warnings.append(f"LinkedIn post is {len(text)} chars, over 3000 limit")
    elif p == "markdown":
        text = draft  # passthrough
    else:
        # generic or unknown — passthrough
        text = draft

    return FormattedOutput(text=text, warnings=warnings)
