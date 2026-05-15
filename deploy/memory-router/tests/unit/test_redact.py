"""Redactor fixture-driven test. Add fixture rows when new classes ship."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flyn_memory_router.redact import redact, REDACTED_PREFIX

FIXTURE = Path(__file__).parent.parent / "fixtures" / "redact_fixture.json"


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text()),
                         ids=lambda c: c["name"])
def test_fixture(case):
    out = redact(case["input"])
    if case["expect_redacted"]:
        assert REDACTED_PREFIX in out, f"expected redaction in {case['name']!r}, got: {out!r}"
        assert case["class"] in out, f"expected class {case['class']!r} in {out!r}"
    else:
        assert REDACTED_PREFIX not in out, f"false positive on {case['name']!r}: {out!r}"


def test_idempotent():
    """Redacting twice is the same as once."""
    s = "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890abcdefgh and a normal sentence"
    assert redact(redact(s)) == redact(s)


def test_empty_string():
    assert redact("") == ""


def test_none_safe():
    assert redact(None) == ""


# Tests for redact_dict function
from flyn_memory_router.redact import redact_dict


def test_redact_dict_string_values():
    out = redact_dict({"a": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890abcdefgh", "b": 1, "c": True})
    assert "[REDACTED:anthropic-key]" in out["a"]
    assert out["b"] == 1
    assert out["c"] is True


def test_redact_dict_nested_dict():
    out = redact_dict({"outer": {"inner": "sk-proj-abcdefghijklmnopqrstuvwxyz123456"}})
    assert "[REDACTED:openai-key]" in out["outer"]["inner"]


def test_redact_dict_dict_in_list():
    """REGRESSION: list-of-dicts must recurse."""
    out = redact_dict({"items": [{"token": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890abcdefgh"}]})
    assert "[REDACTED:anthropic-key]" in out["items"][0]["token"]


def test_redact_dict_list_in_list():
    """list-of-list-of-strings must recurse."""
    out = redact_dict({"groups": [["sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890abcdefgh", "ok"]]})
    assert "[REDACTED:anthropic-key]" in out["groups"][0][0]
    assert out["groups"][0][1] == "ok"


def test_redact_dict_idempotent():
    d = {"a": {"b": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890abcdefgh"},
         "c": [{"d": "sk-proj-abcdefghijklmnopqrstuvwxyz123456"}]}
    assert redact_dict(redact_dict(d)) == redact_dict(d)
