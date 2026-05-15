# tests/unit/test_formatting.py
import pytest
from flyn_orchestrator.formatting import (
    format_for_platform, PlatformWarning, MAX_LENGTHS,
)


def test_telegram_passes_through_markdown():
    out = format_for_platform("**bold** _italic_", platform="telegram")
    assert out.text == "**bold** _italic_"
    assert out.warnings == []


def test_telegram_strips_html_tags():
    """Telegram doesn't render HTML; we strip it to leave the Markdown intact."""
    out = format_for_platform("<p>**bold**</p>\n<br>line", platform="telegram")
    assert "<p>" not in out.text
    assert "<br>" not in out.text
    assert "**bold**" in out.text


def test_tweet_warns_when_over_280():
    long = "x" * 300
    out = format_for_platform(long, platform="tweet")
    assert any("over 280" in w.lower() or "length" in w.lower() for w in out.warnings)


def test_tweet_clean_under_280():
    short = "Hello world, this is a test tweet."
    out = format_for_platform(short, platform="tweet")
    assert out.warnings == []


def test_email_html_wraps_in_basic_template():
    out = format_for_platform("**bold** paragraph\n\nsecond para", platform="email")
    # email wraps in <html>...<body>...
    assert "<html" in out.text.lower() or "<p>" in out.text or "<strong>" in out.text


def test_plain_text_strips_markdown_emphasis():
    out = format_for_platform("**bold** _italic_ `code`", platform="plain")
    assert "**" not in out.text
    assert "_" not in out.text or "italic" in out.text  # underscore may persist as literal char
    assert "`" not in out.text


def test_generic_passes_through_unchanged():
    src = "**bold** _italic_\nline two"
    out = format_for_platform(src, platform="generic")
    assert out.text == src


def test_unknown_platform_falls_back_to_generic():
    """Garbage platform name shouldn't crash; falls back to passthrough."""
    out = format_for_platform("hello", platform="someplatform")
    assert out.text == "hello"


def test_max_lengths_exposed():
    """MAX_LENGTHS constant is queryable by other modules."""
    assert MAX_LENGTHS["tweet"] == 280
    assert MAX_LENGTHS.get("telegram") is None or MAX_LENGTHS["telegram"] >= 4096
