# tests/unit/test_workflows.py
from pathlib import Path
import pytest
from flyn_orchestrator.workflows import (
    Workflow, load_workflow, match_intent, WorkflowNotFound,
)


def _write_dev_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "dev.yaml"
    p.write_text("""
name: dev
intent_patterns:
  - "build"
  - "fix"
  - "add feature"
  - "implement"
roles:
  - name: pm
    model: claude
    prompt: pm_dev
  - name: builder
    model: claude
    prompt: builder
    parallel: true
  - name: reviewer
    model: claude
    prompt: reviewer
    readonly: true
flow:
  - intake
  - discovery
  - plan_approval
  - build
  - review
  - pr
  - human_approval
  - merge
approval_gates:
  plan_approval: teammate
  human_approval: teammate_owns_repo
budget_default_usd: 5.0
""")
    return p


def test_load_workflow(tmp_path):
    p = _write_dev_yaml(tmp_path)
    wf = load_workflow(p)
    assert wf.name == "dev"
    assert "build" in wf.intent_patterns
    assert len(wf.roles) == 3
    assert wf.budget_default_usd == 5.0


def test_load_workflow_missing_file_raises(tmp_path):
    with pytest.raises(WorkflowNotFound):
        load_workflow(tmp_path / "nope.yaml")


def test_match_intent_exact_word(tmp_path):
    wf = load_workflow(_write_dev_yaml(tmp_path))
    assert match_intent("please build a hello.py", [wf]) is wf
    assert match_intent("can you fix the test", [wf]) is wf


def test_match_intent_no_match_returns_none(tmp_path):
    wf = load_workflow(_write_dev_yaml(tmp_path))
    assert match_intent("hello there", [wf]) is None


def test_match_intent_returns_first_winning_workflow(tmp_path):
    p1 = tmp_path / "dev.yaml"
    p1.write_text("name: dev\nintent_patterns: [build]\nroles: []\nflow: []\napproval_gates: {}\nbudget_default_usd: 1.0\n")
    p2 = tmp_path / "research.yaml"
    p2.write_text("name: research\nintent_patterns: [research]\nroles: []\nflow: []\napproval_gates: {}\nbudget_default_usd: 1.0\n")
    workflows = [load_workflow(p1), load_workflow(p2)]
    assert match_intent("please build x", workflows).name == "dev"
    assert match_intent("please research y", workflows).name == "research"


def test_workflow_role_lookup(tmp_path):
    wf = load_workflow(_write_dev_yaml(tmp_path))
    pm = wf.get_role("pm")
    assert pm is not None
    assert pm.model == "claude"
    assert pm.prompt == "pm_dev"
    assert wf.get_role("nonexistent") is None
