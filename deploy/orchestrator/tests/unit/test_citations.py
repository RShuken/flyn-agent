# tests/unit/test_citations.py
import json
import pytest
from flyn_orchestrator.citations import (
    Citation, parse_researcher_output, ResearcherOutput, validate_citations,
)


def test_parse_valid_researcher_json():
    raw = json.dumps({
        "sub_question_id": "Q1",
        "sub_question": "what is postgres",
        "answer": "Postgres is a relational database.",
        "citations": [
            {"url": "https://postgresql.org",
             "title": "PostgreSQL",
             "claim": "It's a relational DB",
             "accessed_at": "2026-05-15"},
        ],
        "confidence": "high",
        "open_questions": [],
    })
    out = parse_researcher_output(raw)
    assert isinstance(out, ResearcherOutput)
    assert out.sub_question_id == "Q1"
    assert len(out.citations) == 1
    assert out.citations[0].url == "https://postgresql.org"


def test_parse_with_fenced_json():
    raw = "```json\n" + json.dumps({
        "sub_question_id": "Q2", "sub_question": "x", "answer": "y",
        "citations": [], "confidence": "low", "open_questions": [],
    }) + "\n```"
    out = parse_researcher_output(raw)
    assert out.sub_question_id == "Q2"


def test_parse_garbage_returns_none():
    assert parse_researcher_output("not json at all") is None
    assert parse_researcher_output("") is None


def test_parse_missing_required_field_returns_none():
    raw = json.dumps({"sub_question_id": "Q1"})  # missing answer, citations, etc
    assert parse_researcher_output(raw) is None


def test_validate_citations_accepts_real_urls():
    cites = [
        Citation(url="https://anthropic.com", title="x", claim="y", accessed_at="2026-05-15"),
        Citation(url="http://example.com/page", title="x", claim="y", accessed_at="2026-05-15"),
    ]
    findings = validate_citations(cites)
    assert findings == []


def test_validate_citations_flags_invalid_urls():
    cites = [
        Citation(url="not-a-url", title="x", claim="y", accessed_at="2026-05-15"),
        Citation(url="bit.ly/xyz", title="x", claim="y", accessed_at="2026-05-15"),
    ]
    findings = validate_citations(cites)
    assert len(findings) == 2
    assert any("not-a-url" in f for f in findings)
    assert any("bit.ly" in f for f in findings)


def test_validate_citations_flags_missing_date():
    cites = [Citation(url="https://x.com", title="x", claim="y", accessed_at="")]
    findings = validate_citations(cites)
    assert any("accessed_at" in f.lower() for f in findings)


def test_validate_citations_flags_duplicates():
    cites = [
        Citation(url="https://x.com", title="x", claim="a", accessed_at="2026-05-15"),
        Citation(url="https://x.com", title="x", claim="b", accessed_at="2026-05-15"),
    ]
    findings = validate_citations(cites)
    assert any("duplicate" in f.lower() for f in findings)
