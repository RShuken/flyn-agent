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
