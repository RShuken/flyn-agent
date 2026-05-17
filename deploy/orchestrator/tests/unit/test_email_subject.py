"""Tests for flyn_orchestrator/adapters/channels/email_subject.py (criteria 6.6)."""
import pytest

from flyn_orchestrator.adapters.channels.email_subject import (
    TAG_APPROVE,
    TAG_REJECT,
    TAG_REPLY,
    TAG_TASK,
    format_subject,
    parse_subject,
)


class TestParseSubject:
    def test_flyn_task_no_task_id(self):
        result = parse_subject("[FLYN-TASK] Start the redesign")
        assert result["tag"] == "FLYN-TASK"
        assert result["task_id"] is None
        assert result["clean_subject"] == "Start the redesign"

    def test_flyn_reply_with_task_id(self):
        result = parse_subject("[FLYN-REPLY:T-0042] bar")
        assert result["tag"] == "FLYN-REPLY"
        assert result["task_id"] == "T-0042"
        assert result["clean_subject"] == "bar"

    def test_flyn_approve_with_task_id(self):
        result = parse_subject("[FLYN-APPROVE:T-0007] looks good")
        assert result["tag"] == "FLYN-APPROVE"
        assert result["task_id"] == "T-0007"

    def test_flyn_reject_with_task_id(self):
        result = parse_subject("[FLYN-REJECT:T-0099] needs changes")
        assert result["tag"] == "FLYN-REJECT"
        assert result["task_id"] == "T-0099"
        assert result["clean_subject"] == "needs changes"

    def test_plain_subject_no_tag(self):
        result = parse_subject("plain subject with no tag")
        assert result["tag"] is None
        assert result["task_id"] is None
        assert result["clean_subject"] == "plain subject with no tag"

    def test_leading_whitespace_before_bracket(self):
        result = parse_subject("  [FLYN-TASK] whitespace test")
        assert result["tag"] == "FLYN-TASK"
        assert result["clean_subject"] == "whitespace test"

    def test_empty_subject(self):
        result = parse_subject("")
        assert result["tag"] is None
        assert result["task_id"] is None
        assert result["clean_subject"] == ""

    def test_tag_only_no_body(self):
        result = parse_subject("[FLYN-TASK]")
        assert result["tag"] == "FLYN-TASK"
        assert result["task_id"] is None
        assert result["clean_subject"] == ""

    def test_task_id_with_underscores(self):
        result = parse_subject("[FLYN-REPLY:TASK_001] message")
        assert result["task_id"] == "TASK_001"


class TestFormatSubject:
    def test_tag_without_task_id(self):
        result = format_subject(TAG_TASK, None, "Start the redesign")
        assert result == "[FLYN-TASK] Start the redesign"

    def test_tag_with_task_id(self):
        result = format_subject(TAG_REPLY, "T-0042", "Re: Start the redesign")
        assert result == "[FLYN-REPLY:T-0042] Re: Start the redesign"

    def test_approve_tag(self):
        result = format_subject(TAG_APPROVE, "T-0007", "looks good")
        assert result == "[FLYN-APPROVE:T-0007] looks good"

    def test_reject_tag(self):
        result = format_subject(TAG_REJECT, "T-0099", "needs changes")
        assert result == "[FLYN-REJECT:T-0099] needs changes"

    def test_roundtrip_parse_format(self):
        """format_subject → parse_subject should be a round-trip."""
        original = format_subject(TAG_REPLY, "T-0042", "bar")
        parsed = parse_subject(original)
        assert parsed["tag"] == TAG_REPLY
        assert parsed["task_id"] == "T-0042"
        assert parsed["clean_subject"] == "bar"
