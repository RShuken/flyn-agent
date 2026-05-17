"""Tests for flyn_orchestrator/adapters/channels/email_auth.py (criteria 6.5)."""
import pytest

from flyn_orchestrator.adapters.channels.email_auth import (
    parse_authentication_results,
    verify_email_auth,
)

# ---------------------------------------------------------------------------
# parse_authentication_results
# ---------------------------------------------------------------------------

REAL_GMAIL_HEADER = (
    "mx.google.com;"
    " dkim=pass header.i=@getcora.io header.s=default;"
    " spf=pass (google.com: domain of noreply@getcora.io designates 1.2.3.4"
    " as permitted sender) smtp.mailfrom=noreply@getcora.io"
)


class TestParseAuthenticationResults:
    def test_real_header_spf_pass_dkim_pass(self):
        headers = {"Authentication-Results": REAL_GMAIL_HEADER}
        result = parse_authentication_results(headers)
        assert result["spf"] == "pass"
        assert result["dkim"] == "pass"
        assert REAL_GMAIL_HEADER in result["raw"]

    def test_lowercase_header_name(self):
        headers = {"authentication-results": REAL_GMAIL_HEADER}
        result = parse_authentication_results(headers)
        assert result["spf"] == "pass"
        assert result["dkim"] == "pass"

    def test_empty_header_dict(self):
        result = parse_authentication_results({})
        assert result == {"spf": "unknown", "dkim": "unknown", "raw": ""}

    def test_header_present_but_empty_string(self):
        result = parse_authentication_results({"Authentication-Results": ""})
        assert result == {"spf": "unknown", "dkim": "unknown", "raw": ""}

    def test_spf_fail_dkim_pass(self):
        raw = "mx.example.com; spf=fail; dkim=pass header.i=@example.com"
        result = parse_authentication_results({"Authentication-Results": raw})
        assert result["spf"] == "fail"
        assert result["dkim"] == "pass"

    def test_spf_pass_dkim_missing(self):
        raw = "mx.example.com; spf=pass smtp.mailfrom=foo@example.com"
        result = parse_authentication_results({"Authentication-Results": raw})
        assert result["spf"] == "pass"
        assert result["dkim"] == "unknown"

    def test_spf_softfail(self):
        raw = "mx.example.com; spf=softfail; dkim=none"
        result = parse_authentication_results({"Authentication-Results": raw})
        assert result["spf"] == "softfail"
        assert result["dkim"] == "none"


# ---------------------------------------------------------------------------
# verify_email_auth
# ---------------------------------------------------------------------------

ALLOWLIST = frozenset({"trusted@example.com", "admin@example.com"})


class TestVerifyEmailAuth:
    def test_allowlisted_sender_passes_even_with_no_auth(self):
        allowed, reason = verify_email_auth({}, "trusted@example.com", allowlist=ALLOWLIST)
        assert allowed is True
        assert reason == "allowlist"

    def test_allowlisted_sender_case_insensitive(self):
        allowed, reason = verify_email_auth({}, "TRUSTED@EXAMPLE.COM", allowlist=ALLOWLIST)
        assert allowed is True
        assert reason == "allowlist"

    def test_allowlisted_sender_passes_even_with_spf_fail(self):
        headers = {"Authentication-Results": "mx.x.com; spf=fail; dkim=fail"}
        allowed, reason = verify_email_auth(headers, "trusted@example.com", allowlist=ALLOWLIST)
        assert allowed is True
        assert reason == "allowlist"

    def test_non_allowlisted_spf_fail_rejected(self):
        headers = {"Authentication-Results": "mx.x.com; spf=fail; dkim=pass"}
        allowed, reason = verify_email_auth(headers, "stranger@evil.com", allowlist=ALLOWLIST)
        assert allowed is False
        assert "fail" in reason

    def test_non_allowlisted_dkim_fail_rejected(self):
        headers = {"Authentication-Results": "mx.x.com; spf=pass; dkim=fail"}
        allowed, reason = verify_email_auth(headers, "stranger@evil.com", allowlist=ALLOWLIST)
        assert allowed is False
        assert "fail" in reason

    def test_non_allowlisted_spf_pass_dkim_unknown_allowed(self):
        headers = {"Authentication-Results": "mx.x.com; spf=pass"}
        allowed, reason = verify_email_auth(headers, "stranger@legit.com", allowlist=ALLOWLIST)
        assert allowed is True
        assert "ok" in reason

    def test_non_allowlisted_spf_unknown_dkim_pass_allowed(self):
        headers = {"Authentication-Results": "mx.x.com; dkim=pass header.i=@legit.com"}
        allowed, reason = verify_email_auth(headers, "stranger@legit.com", allowlist=ALLOWLIST)
        assert allowed is True

    def test_non_allowlisted_both_unknown_rejected(self):
        allowed, reason = verify_email_auth({}, "stranger@evil.com", allowlist=ALLOWLIST)
        assert allowed is False
        assert "no auth headers" in reason

    def test_non_allowlisted_both_pass_allowed(self):
        headers = {"Authentication-Results": REAL_GMAIL_HEADER}
        allowed, reason = verify_email_auth(headers, "legit@getcora.io", allowlist=ALLOWLIST)
        assert allowed is True

    def test_default_allowlist_empty_when_none(self):
        """When allowlist=None the default frozenset() is used — stranger still evaluated by auth."""
        headers = {"Authentication-Results": "mx.x.com; spf=pass; dkim=pass"}
        allowed, reason = verify_email_auth(headers, "stranger@legit.com", allowlist=None)
        assert allowed is True
