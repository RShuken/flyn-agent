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


def test_discover_entities_from_vault_parses_wikilinks(tmp_path):
    from flyn_memory_router.lint import discover_entities_from_vault
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text(
        "# Index\n\n## People\n- [[beth]]\n- [[eric]]\n\n"
        "## Projects\n- [[openlit]]\n"
    )
    entities = discover_entities_from_vault(tmp_path)
    assert set(entities) == {"beth", "eric", "openlit"}


def test_discover_entities_returns_empty_when_no_index(tmp_path):
    from flyn_memory_router.lint import discover_entities_from_vault
    assert discover_entities_from_vault(tmp_path) == []


def test_discover_entities_dedupes_repeats(tmp_path):
    from flyn_memory_router.lint import discover_entities_from_vault
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("- [[beth]]\n- [[beth]]\n- [[Eric]]\n")
    entities = discover_entities_from_vault(tmp_path)
    # case-preserving but dedup-by-exact-text
    assert sorted(entities) == ["Eric", "beth"]
