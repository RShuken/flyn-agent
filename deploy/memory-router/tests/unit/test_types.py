"""Type-validation tests for InboundEvent and Tier."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from flyn_memory_router.types import InboundEvent, Tier


def test_inbound_event_minimal():
    e = InboundEvent(
        source="orchestrator",
        event_type="task_created",
        subject="T-0042",
        body="Beth opened task T-0042 in the dev workflow.",
        dedup_key="orch-T-0042-created",
    )
    assert e.source == "orchestrator"
    assert e.importance is None  # router infers when absent


def test_inbound_event_rejects_empty_body():
    with pytest.raises(ValidationError):
        InboundEvent(
            source="orchestrator",
            event_type="task_created",
            subject="T-0042",
            body="",
            dedup_key="x",
        )


def test_inbound_event_rejects_unknown_importance():
    with pytest.raises(ValidationError):
        InboundEvent(
            source="orchestrator",
            event_type="task_created",
            subject="T-0042",
            body="anything",
            dedup_key="x",
            importance="medium",  # not in the enum
        )


def test_tier_enum_values():
    assert {t.value for t in Tier} == {"hot", "warm", "cool", "cold", "lesson"}


def test_hit_minimal():
    from flyn_memory_router.types import Hit
    h = Hit(text="Beth Kukla, COO Cora", source="hot/MEMORY.md", score=0.9, metadata={})
    assert h.source == "hot/MEMORY.md"
    assert h.score == 0.9


def test_hit_requires_source_namespace():
    from flyn_memory_router.types import Hit
    with pytest.raises(ValidationError):
        Hit(text="x", source="", score=0.5, metadata={})


def test_query_result_shape():
    from flyn_memory_router.types import Hit, QueryResult, SourceError
    qr = QueryResult(
        query_id="qid-1",
        hits=[Hit(text="a", source="hot/MEMORY.md", score=0.9, metadata={})],
        source_errors=[SourceError(source="lossless", error_class="timeout", message="2s")],
        elapsed_ms=42,
    )
    assert qr.query_id == "qid-1"
    assert len(qr.hits) == 1
    assert qr.source_errors[0].error_class == "timeout"


def test_lint_report_shape():
    from flyn_memory_router.types import LintReport, LintFinding
    lr = LintReport(findings=[LintFinding(
        entity="Beth",
        sources={"hot/MEMORY.md": "COO Cora", "warm/graphiti": "Co-Founder"},
        divergence="graphiti missing 'COO Cora' attribute",
        suggested_fix="update Graphiti episode 'beth-intro-2026-04'",
    )])
    assert len(lr.findings) == 1
