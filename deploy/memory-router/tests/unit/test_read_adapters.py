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
