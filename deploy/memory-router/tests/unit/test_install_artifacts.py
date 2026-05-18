from __future__ import annotations

from pathlib import Path


def test_writes_auto_memory_pointer_when_missing(tmp_path: Path):
    from flyn_memory_router.discovery import write_auto_memory_pointer
    memdir = tmp_path / "memory"
    write_auto_memory_pointer(memdir)
    f = memdir / "feedback_memory_router.md"
    assert f.exists()
    content = f.read_text()
    assert "memory-router-front-door" in content
    assert "flyn-mem query" in content


def test_appends_to_memory_md_index_only_once(tmp_path: Path):
    from flyn_memory_router.discovery import append_memory_md_index
    memdir = tmp_path / "memory"
    memdir.mkdir()
    idx = memdir / "MEMORY.md"
    idx.write_text("- [other](other.md) — example\n")
    append_memory_md_index(memdir)
    append_memory_md_index(memdir)
    text = idx.read_text()
    assert text.count("feedback_memory_router.md") == 1


def test_writes_tools_md_pointer_only_once(tmp_path: Path):
    from flyn_memory_router.discovery import append_tools_md
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = workspace / "TOOLS.md"
    tools.write_text("# TOOLS\n\nExisting content.\n")
    append_tools_md(workspace)
    append_tools_md(workspace)
    text = tools.read_text()
    assert text.count("## flyn-mem") == 1
