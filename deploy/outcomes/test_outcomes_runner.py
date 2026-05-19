"""Tests for outcomes_runner rubric parsing (B6)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from outcomes_runner import parse_phase, parse_checklist, score_only


def test_parse_5col_table_format(tmp_path: Path):
    """Current orchestrator rubric: | id | criterion | status | evidence | gap |"""
    rubric = tmp_path / "r.md"
    rubric.write_text(
        "## Phase 5 — Ops workflow\n"
        "\n"
        "| # | Criterion | Status | Evidence | Gap |\n"
        "|---|---|---|---|---|\n"
        "| 5.1 | `workflows/ops.yaml` policy | ✅ | file exists | — |\n"
        "| 5.2 | Role prompts | 🟡 | partial | needs review |\n"
        "| 5.3 | risk-rules.yaml | ⬜ | not yet | TBD |\n"
    )
    info = parse_phase(rubric, 5)
    assert info["title"] == "Ops workflow"
    assert len(info["criteria"]) == 3
    by_id = {c["id"]: c for c in info["criteria"]}
    assert by_id["5.1"]["status"] == "done"
    assert by_id["5.2"]["status"] == "in_progress"
    assert by_id["5.3"]["status"] == "todo"
    assert by_id["5.1"]["criterion"] == "`workflows/ops.yaml` policy"
    assert by_id["5.1"]["test"] == "file exists"  # evidence-as-test
    assert by_id["5.3"]["gap"] == "TBD"


def test_parse_4col_legacy_format(tmp_path: Path):
    """Legacy: | id <status?> | criterion | test |"""
    rubric = tmp_path / "r.md"
    rubric.write_text(
        "## Phase 2 — Dev workflow\n"
        "\n"
        "| # | Criterion | Test |\n"
        "|---|---|---|\n"
        "| 2.1 ✅ | builds pass | `pytest tests/` |\n"
        "| 2.2 | something | something else |\n"
    )
    info = parse_phase(rubric, 2)
    assert len(info["criteria"]) == 2
    by_id = {c["id"]: c for c in info["criteria"]}
    assert by_id["2.1"]["status"] == "done"
    assert by_id["2.2"]["status"] == "todo"  # no emoji = todo


def test_parse_checklist_format(tmp_path: Path):
    """Checklist: `- [ ]` and `- [x]` with section headers as id prefixes."""
    rubric = tmp_path / "r.md"
    rubric.write_text(
        "# My Rubric\n"
        "\n"
        "## Types & adapter contracts\n"
        "\n"
        "- [x] Hit exists\n"
        "- [ ] QueryResult exists\n"
        "\n"
        "## Adapters built\n"
        "\n"
        "- [ ] hot_read exists\n"
    )
    info = parse_checklist(rubric)
    assert len(info["criteria"]) == 3
    by_id = {c["id"]: c for c in info["criteria"]}
    assert "types-adapter-contracts.1" in by_id
    assert by_id["types-adapter-contracts.1"]["status"] == "done"
    assert by_id["types-adapter-contracts.2"]["status"] == "todo"
    assert by_id["adapters-built.1"]["status"] == "todo"


def test_score_only_table(tmp_path: Path):
    """score_only returns counts + percent without firing any LLM calls."""
    rubric = tmp_path / "r.md"
    rubric.write_text(
        "## Phase 5 — Ops\n\n"
        "| # | Criterion | Status | Evidence | Gap |\n"
        "|---|---|---|---|---|\n"
        "| 5.1 | a | ✅ | x | — |\n"
        "| 5.2 | b | ✅ | y | — |\n"
        "| 5.3 | c | ⬜ | z | TBD |\n"
    )
    result = score_only(rubric, phase=5)
    assert result["counts"]["done"] == 2
    assert result["counts"]["todo"] == 1
    assert result["total"] == 3
    assert result["percent_done"] == pytest.approx(66.7, abs=0.1)
    assert result["unmet"] == ["5.3"]


def test_score_only_checklist(tmp_path: Path):
    rubric = tmp_path / "r.md"
    rubric.write_text(
        "## A\n- [x] one\n- [ ] two\n\n## B\n- [ ] three\n"
    )
    result = score_only(rubric, checklist=True)
    assert result["counts"]["done"] == 1
    assert result["counts"]["todo"] == 2
    assert result["total"] == 3
    assert set(result["unmet"]) == {"a.2", "b.1"}


def test_score_only_table_requires_phase(tmp_path: Path):
    rubric = tmp_path / "r.md"
    rubric.write_text("## Phase 1 — X\n| 1.1 | a | ✅ | b | — |\n")
    with pytest.raises(SystemExit, match="--phase is required"):
        score_only(rubric)  # no phase, no checklist=True


def test_parse_phase_handles_real_orchestrator_rubric():
    """Smoke test against the actual rubric file in the repo."""
    real = Path(__file__).parent / "ORCHESTRATOR-PHASE-RUBRIC.md"
    if not real.exists():
        pytest.skip("real rubric not at expected path")
    info = parse_phase(real, 5)
    assert info["title"]  # found Phase 5
    # All 9 Phase 5 criteria should now be 'done' (rubric flipped 5.9 ✅ on 2026-05-18)
    statuses = [c["status"] for c in info["criteria"]]
    assert statuses.count("done") == 9
    assert statuses.count("todo") == 0
