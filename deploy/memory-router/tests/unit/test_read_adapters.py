"""Per-adapter unit tests. Each adapter test class is added by its own task."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


class TestHotRead:
    def test_finds_hit_in_memory_md(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        md = tmp_path / "MEMORY.md"
        md.write_text(
            "# MEMORY\n\n## Beth\nBeth Kukla, COO Cora, PM for OL.\n\n"
            "## Eric\nEric Schneider, tech lead at FPS.\n"
        )
        pin_file = tmp_path / "pins.json"
        pin_file.write_text("[]")
        hr = HotRead(memory_md=md, pin_file=pin_file)
        hits = asyncio.run(hr.query("Beth"))
        assert any("Beth Kukla" in h.text for h in hits)

    def test_returns_empty_when_no_match(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        md = tmp_path / "MEMORY.md"
        md.write_text("# MEMORY\n\n## A\nsomething\n")
        pin_file = tmp_path / "pins.json"
        pin_file.write_text("[]")
        hits = asyncio.run(HotRead(memory_md=md, pin_file=pin_file).query("nonexistent"))
        assert hits == []

    def test_pins_have_higher_score_than_sections(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        md = tmp_path / "MEMORY.md"
        md.write_text("# MEMORY\n\n## Beth\nBeth in a section.\n")
        pin_file = tmp_path / "pins.json"
        pin_file.write_text(json.dumps([{"subject": "Beth", "body": "Beth is pinned", "ts": 0}]))
        hits = asyncio.run(HotRead(memory_md=md, pin_file=pin_file).query("Beth"))
        assert hits[0].source == "hot/pins"

    def test_missing_files_gracefully_return_empty(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        hits = asyncio.run(HotRead(
            memory_md=tmp_path / "missing.md",
            pin_file=tmp_path / "missing.json",
        ).query("anything"))
        assert hits == []


class TestWarmRead:
    @pytest.mark.asyncio
    async def test_calls_graphiti_and_returns_hits(self, tmp_path: Path):
        from flyn_memory_router.adapters.warm_read import WarmRead
        import httpx

        fixture_path = Path(__file__).parent.parent / "fixtures" / "mock_graphiti_search.json"
        fixture = json.loads(fixture_path.read_text())

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/search"
            assert request.url.params["q"] == "Beth"
            return httpx.Response(200, json=fixture)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            wr = WarmRead(
                graphiti_url="http://test-graphiti",
                workspace_memory_dir=tmp_path,
                http=client,
            )
            hits = await wr.query("Beth")

        graphiti_hits = [h for h in hits if h.source == "warm/graphiti"]
        assert len(graphiti_hits) >= 2
        assert graphiti_hits[0].metadata.get("canonical_id") == "ep-1"

    @pytest.mark.asyncio
    async def test_workspace_memory_grep_returns_hits(self, tmp_path: Path):
        from flyn_memory_router.adapters.warm_read import WarmRead
        import httpx

        (tmp_path / "2026-05-13.md").write_text("Beth status: PM. Linear: 73/124.")

        async def handler(request):
            return httpx.Response(200, json={"results": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            wr = WarmRead(graphiti_url="http://t", workspace_memory_dir=tmp_path, http=client)
            hits = await wr.query("Beth")
        ws_hits = [h for h in hits if h.source == "warm/workspace"]
        assert len(ws_hits) == 1
        assert "Linear" in ws_hits[0].text

    @pytest.mark.asyncio
    async def test_graphiti_5xx_does_not_block_workspace_grep(self, tmp_path: Path):
        from flyn_memory_router.adapters.warm_read import WarmRead
        import httpx

        (tmp_path / "x.md").write_text("Beth note")

        async def handler(request):
            return httpx.Response(503, json={"detail": "unavailable"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            wr = WarmRead(graphiti_url="http://t", workspace_memory_dir=tmp_path, http=client)
            hits = await wr.query("Beth")
        assert any(h.source == "warm/workspace" for h in hits)


class TestCoolRead:
    def test_grep_daily_rollups(self, tmp_path: Path):
        from flyn_memory_router.adapters.cool_read import CoolRead
        (tmp_path / "2026-05-10.md").write_text("Beth pinged about Linear.")
        (tmp_path / "2026-05-11.md").write_text("Eric posted Pearl Platform update.")
        hits = asyncio.run(CoolRead(memory_dir=tmp_path).query("Beth"))
        assert len(hits) == 1
        assert hits[0].source == "cool/rollup"
        assert "2026-05-10" in hits[0].metadata.get("date", "")


class TestColdRead:
    def test_line_grep_captures_index(self, tmp_path: Path):
        from flyn_memory_router.adapters.cold_read import ColdRead
        idx = tmp_path / "captures_index.jsonl"
        idx.write_text(
            json.dumps({"ts": "2026-04-01", "subject": "Beth onboard", "summary": "..."}) + "\n"
            + json.dumps({"ts": "2026-04-02", "subject": "Eric onboard", "summary": "..."}) + "\n"
        )
        hits = asyncio.run(ColdRead(index_path=idx).query("Beth"))
        assert len(hits) == 1
        assert hits[0].source == "cold/captures"


class TestLessonRead:
    def test_grep_knowledge_dir(self, tmp_path: Path):
        from flyn_memory_router.adapters.lesson_read import LessonRead
        (tmp_path / "lesson-mcp-failure.md").write_text(
            "## Lesson 2026-04-21\nMCP tool_use fails with codex; use REST."
        )
        hits = asyncio.run(LessonRead(knowledge_dir=tmp_path).query("MCP"))
        assert len(hits) == 1
        assert hits[0].source == "lesson/KNOWLEDGE"


class TestReferenceRead:
    @pytest.fixture
    def vault(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "reference_vault"

    def test_reads_index_first_then_walks(self, vault: Path):
        from flyn_memory_router.adapters.reference_read import ReferenceRead
        hits = asyncio.run(ReferenceRead(vault=vault).query("Beth"))
        assert any(h.metadata.get("file", "").endswith("beth.md") for h in hits)
        assert all(h.source == "reference/wiki" for h in hits)

    def test_follows_wikilinks(self, vault: Path):
        from flyn_memory_router.adapters.reference_read import ReferenceRead
        hits = asyncio.run(ReferenceRead(vault=vault).query("openlit"))
        assert any(h.metadata.get("file", "").endswith("openlit.md") for h in hits)

    def test_returns_empty_without_index(self, tmp_path: Path):
        from flyn_memory_router.adapters.reference_read import ReferenceRead
        assert asyncio.run(ReferenceRead(vault=tmp_path).query("anything")) == []


class TestUserRead:
    @pytest.fixture
    def memdir(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "auto_memory"

    def test_grep_finds_match(self, memdir: Path):
        from flyn_memory_router.adapters.user_read import UserRead
        hits = asyncio.run(UserRead(auto_memory_dir=memdir).query("Beth"))
        assert any("Beth" in h.text for h in hits)
        assert all(h.source == "user/auto-memory" for h in hits)

    def test_frontmatter_aware_metadata(self, memdir: Path):
        from flyn_memory_router.adapters.user_read import UserRead
        hits = asyncio.run(UserRead(auto_memory_dir=memdir).query("Beth"))
        beth_hits = [h for h in hits if h.metadata.get("name") == "beth-role"]
        assert beth_hits
        assert beth_hits[0].metadata.get("memory_type") == "user"

    def test_skips_memory_md_index_file(self, memdir: Path):
        from flyn_memory_router.adapters.user_read import UserRead
        hits = asyncio.run(UserRead(auto_memory_dir=memdir).query("Beth"))
        assert all("MEMORY.md" not in h.metadata.get("file", "") for h in hits)


class TestOLWikiRead:
    @pytest.mark.asyncio
    async def test_sends_pin_header_and_returns_hits(self):
        from flyn_memory_router.adapters.ol_wiki_read import OLWikiRead
        import httpx

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("X-OL-Wiki-Pin") == "1080"
            assert request.url.params["q"] == "Linear"
            return httpx.Response(200, json={"results": [
                {"id": "Q-42", "section": "I", "question": "Linear plan?",
                 "answer": "Free tier blocks at 250.", "score": 0.85},
            ]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = OLWikiRead(url="http://test-olwiki", pin="1080", http=client)
            hits = await r.query("Linear")
        assert len(hits) == 1
        assert hits[0].source == "ol_wiki"
        assert hits[0].metadata.get("question_id") == "Q-42"

    @pytest.mark.asyncio
    async def test_5xx_returns_empty(self):
        from flyn_memory_router.adapters.ol_wiki_read import OLWikiRead
        import httpx

        async def handler(request):
            return httpx.Response(503, json={"detail": "down"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = OLWikiRead(url="http://t", pin="1080", http=client)
            assert await r.query("anything") == []
