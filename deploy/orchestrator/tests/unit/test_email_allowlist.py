"""Tests for the CONTACTS.md-driven email allowlist loader.

Closes the §Δ.6-partial threat: "allowlist hardcoded vs CONTACTS.md".
"""
from __future__ import annotations
from pathlib import Path

import pytest

from flyn_orchestrator.adapters.channels.email_allowlist import (
    _parse_allowlist,
    load_allowlist_from_contacts,
)


# ---------------------------------------------------------------------------
# _parse_allowlist (string-level unit tests)
# ---------------------------------------------------------------------------

def test_parse_extracts_emails_from_canonical_section():
    """The canonical heading + bullet format from CONTACTS.md works."""
    text = """
# Some header

## Email allowlist (Flyn EmailChannelAdapter)

These addresses bypass SPF/DKIM.

- ryanshuken@gmail.com
- beth@cora.community
- eric@cora.community

## Next section
"""
    result = _parse_allowlist(text)
    assert result == frozenset({
        "ryanshuken@gmail.com",
        "beth@cora.community",
        "eric@cora.community",
    })


def test_parse_returns_none_when_section_missing():
    """No heading containing 'email allowlist' → None (fall back to default)."""
    text = "# CONTACTS\n\nNo allowlist section here.\n"
    result = _parse_allowlist(text)
    assert result is None


def test_parse_returns_empty_set_for_explicit_empty_section():
    """Heading present but no bullets → explicit empty allowlist (reject all)."""
    text = """
## Email allowlist (Flyn EmailChannelAdapter)

No emails listed.

## Next section
"""
    result = _parse_allowlist(text)
    assert result == frozenset()


def test_parse_skips_tbd_bullets():
    """Bullets without an email-shaped address are skipped (e.g., 'TBD')."""
    text = """
## Email allowlist (Flyn EmailChannelAdapter)

- ryanshuken@gmail.com
- TBD (pending DNS provisioning)
- another@valid.email
- placeholder
"""
    result = _parse_allowlist(text)
    assert result == frozenset({"ryanshuken@gmail.com", "another@valid.email"})


def test_parse_skips_html_comments():
    """Bullets inside `<!-- ... -->` comment blocks are ignored."""
    text = """
## Email allowlist (Flyn EmailChannelAdapter)

- realemail@example.com

<!--
- commented@example.com
- another-commented@example.com
-->

- second-real@example.com
"""
    result = _parse_allowlist(text)
    assert result == frozenset({"realemail@example.com", "second-real@example.com"})


def test_parse_case_insensitive_heading():
    """Heading matcher is case-insensitive."""
    text = """
## EMAIL ALLOWLIST (something)

- foo@bar.com
"""
    result = _parse_allowlist(text)
    assert result == frozenset({"foo@bar.com"})


def test_parse_handles_asterisk_bullets():
    """Markdown allows `*` as a bullet too."""
    text = """
## Email allowlist

* asterisk@example.com
- dash@example.com
"""
    result = _parse_allowlist(text)
    assert result == frozenset({"asterisk@example.com", "dash@example.com"})


def test_parse_lowercases_emails():
    """Emails are normalized to lowercase for case-insensitive matching."""
    text = """
## Email allowlist

- Mixed.Case@Example.COM
"""
    result = _parse_allowlist(text)
    assert result == frozenset({"mixed.case@example.com"})


def test_parse_stops_at_next_heading():
    """Bullets under a sibling heading are not absorbed."""
    text = """
## Email allowlist

- in-allowlist@example.com

## Other contacts

- not-in-allowlist@example.com
"""
    result = _parse_allowlist(text)
    assert result == frozenset({"in-allowlist@example.com"})


# ---------------------------------------------------------------------------
# load_allowlist_from_contacts (file-system layer)
# ---------------------------------------------------------------------------

def test_load_returns_none_when_file_missing(tmp_path):
    result = load_allowlist_from_contacts(tmp_path / "missing.md")
    assert result is None


def test_load_returns_parsed_set_from_real_file(tmp_path):
    contacts = tmp_path / "CONTACTS.md"
    contacts.write_text(
        "# CONTACTS\n\n## Email allowlist\n\n- a@b.com\n- c@d.com\n",
        encoding="utf-8",
    )
    result = load_allowlist_from_contacts(contacts)
    assert result == frozenset({"a@b.com", "c@d.com"})


def test_load_handles_unicode_decode_error_gracefully(tmp_path):
    """Binary garbage in CONTACTS.md returns None rather than raising."""
    contacts = tmp_path / "CONTACTS.md"
    contacts.write_bytes(b"\xfe\xff\x00\x01\x02 not valid utf-8")
    result = load_allowlist_from_contacts(contacts)
    assert result is None


# ---------------------------------------------------------------------------
# EmailChannelAdapter integration
# ---------------------------------------------------------------------------

def test_adapter_uses_contacts_path_when_provided(tmp_path):
    """When contacts_path is given and parses to a non-empty set, the adapter
    uses that as its allowlist."""
    from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter
    contacts = tmp_path / "CONTACTS.md"
    contacts.write_text(
        "## Email allowlist\n\n- from-contacts@example.com\n", encoding="utf-8",
    )
    adapter = EmailChannelAdapter(config=None, contacts_path=contacts)
    assert adapter._allowlist == frozenset({"from-contacts@example.com"})


def test_adapter_falls_back_to_default_when_contacts_missing(tmp_path):
    """When contacts_path doesn't exist, fall back to DEFAULT_ALLOWLIST."""
    from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter, DEFAULT_ALLOWLIST
    adapter = EmailChannelAdapter(config=None, contacts_path=tmp_path / "nope.md")
    assert adapter._allowlist == DEFAULT_ALLOWLIST


def test_adapter_falls_back_when_contacts_has_no_allowlist_section(tmp_path):
    """File present but no 'Email allowlist' heading → use DEFAULT_ALLOWLIST."""
    from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter, DEFAULT_ALLOWLIST
    contacts = tmp_path / "CONTACTS.md"
    contacts.write_text("# Contacts\n\nNo allowlist here.\n", encoding="utf-8")
    adapter = EmailChannelAdapter(config=None, contacts_path=contacts)
    assert adapter._allowlist == DEFAULT_ALLOWLIST


def test_adapter_explicit_allowlist_overrides_contacts(tmp_path):
    """When `allowlist=` is passed explicitly, contacts_path is ignored."""
    from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter
    contacts = tmp_path / "CONTACTS.md"
    contacts.write_text(
        "## Email allowlist\n\n- from-contacts@example.com\n", encoding="utf-8",
    )
    explicit = frozenset({"explicit-only@example.com"})
    adapter = EmailChannelAdapter(config=None, allowlist=explicit, contacts_path=contacts)
    assert adapter._allowlist == explicit


def test_adapter_honors_empty_section_as_reject_all(tmp_path):
    """Section present but no emails → frozenset() → adapter rejects every sender
    (except those passing real SPF/DKIM, which the adapter still checks)."""
    from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter
    contacts = tmp_path / "CONTACTS.md"
    contacts.write_text(
        "## Email allowlist (Flyn EmailChannelAdapter)\n\n(intentionally empty)\n",
        encoding="utf-8",
    )
    adapter = EmailChannelAdapter(config=None, contacts_path=contacts)
    assert adapter._allowlist == frozenset()
