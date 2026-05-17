from __future__ import annotations

import pytest

from flyn_memory_router.types import Hit


@pytest.mark.asyncio
async def test_no_drift_when_sources_agree():
    from flyn_memory_router.lint import detect_drift
    per_source = {
        "hot/MEMORY.md": [Hit(text="Beth Kukla, COO Cora", source="hot/MEMORY.md", score=0.9, metadata={})],
        "warm/graphiti": [Hit(text="Beth Kukla, COO Cora", source="warm/graphiti", score=0.9, metadata={})],
    }
    assert await detect_drift("Beth", per_source) == []


@pytest.mark.asyncio
async def test_drift_when_sources_diverge_substantially():
    from flyn_memory_router.lint import detect_drift
    per_source = {
        "hot/MEMORY.md":   [Hit(text="Beth = COO Cora, PM for OL", source="hot/MEMORY.md", score=0.9, metadata={})],
        "warm/graphiti":   [Hit(text="Beth is a contractor", source="warm/graphiti", score=0.9, metadata={})],
    }
    findings = await detect_drift("Beth", per_source)
    assert len(findings) == 1
    assert findings[0].entity == "Beth"


@pytest.mark.asyncio
async def test_no_finding_when_only_one_source_has_data():
    from flyn_memory_router.lint import detect_drift
    per_source = {
        "hot/MEMORY.md": [Hit(text="Beth = COO Cora", source="hot/MEMORY.md", score=0.9, metadata={})],
        "warm/graphiti": [],
    }
    assert await detect_drift("Beth", per_source) == []
