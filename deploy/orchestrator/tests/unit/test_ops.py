"""Unit tests for ops.py orchestration helpers.

Uses stub backend — no live LLM calls.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from flyn_orchestrator.ops import (
    spec_ops_action, classify_risk, dry_run_action, execute_action, validate_action,
    OpsSpec, RiskAssessment, DryRunResult, ExecuteResult,
    ValidatorResult, PostConditionResult,
)
from flyn_orchestrator.risk_tier import RuleSet, RiskRule
from flyn_orchestrator.audit import SnapshotBundle
from flyn_orchestrator.backends.base import WorkerResult


def _stub_backend(summary_text: str):
    b = MagicMock()
    b.name = "stub"

    def _run(spec, prompt, *, cost_tracker=None):
        cap = Path(spec.worktree_path) / f"{spec.worker_id}.jsonl"
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text(json.dumps({"type": "result", "result": summary_text}))
        return WorkerResult(
            worker_id=spec.worker_id, exit_code=0, capture_path=cap,
            cost_usd=0.01, duration_ms=10, changed_files=[], summary=summary_text,
        )

    b.run = _run
    return b


# ---- 1. spec_ops_action ----

def test_spec_ops_action_parses_pm_output(tmp_path):
    pm_json = json.dumps({
        "title": "Rotate test token",
        "rationale": "monthly rotation",
        "target": "/tmp/test-token.txt",
        "action": "Replace contents with new token",
        "preconditions": ["file exists"],
        "postconditions": ["file content matches new token"],
        "rollback": "restore from backup",
        "dry_run_supported": True,
        "estimated_blast_radius": "scoped to /tmp/test-token.txt",
        "external_calls": [],
    })
    spec = spec_ops_action("rotate the test token", scratch_dir=tmp_path,
                            backend=_stub_backend(pm_json))
    assert spec is not None
    assert spec.title == "Rotate test token"
    assert spec.target == "/tmp/test-token.txt"
    assert spec.dry_run_supported is True
    assert len(spec.postconditions) == 1


def test_spec_ops_action_garbage_returns_none(tmp_path):
    assert spec_ops_action("anything", scratch_dir=tmp_path,
                            backend=_stub_backend("not json")) is None


# ---- 2. classify_risk ----

def test_classify_risk_uses_rule_floor(tmp_path):
    """Rule says 'low'; LLM returns 'low'; result is 'low'."""
    rules = RuleSet(default_tier="medium", rules=[
        RiskRule(pattern="test.*token", tier="low", reason="test"),
    ])
    spec = OpsSpec(
        title="x", rationale="x", target="/tmp/x", action="x",
        preconditions=[], postconditions=[], rollback="x",
        dry_run_supported=True, estimated_blast_radius="x", external_calls=[],
    )
    llm_json = json.dumps({"tier": "low", "reason": "agrees with rule",
                            "upgraded_from_rule": False})
    backend = _stub_backend(llm_json)
    result = classify_risk("rotate the test token", spec, rules=rules,
                           scratch_dir=tmp_path, backend=backend)
    assert result.tier == "low"
    assert result.upgraded_from_rule is False


def test_classify_risk_llm_upgrades_floor(tmp_path):
    """Rule says 'medium'; LLM returns 'high'. Result is 'high', upgraded=True."""
    rules = RuleSet(default_tier="medium", rules=[])
    spec = OpsSpec(
        title="x", rationale="x", target="prod.db", action="x",
        preconditions=[], postconditions=[], rollback="x",
        dry_run_supported=True,
        estimated_blast_radius="production database", external_calls=[],
    )
    llm_json = json.dumps({"tier": "high", "reason": "production blast radius",
                            "upgraded_from_rule": True})
    backend = _stub_backend(llm_json)
    result = classify_risk("ambiguous intent", spec, rules=rules,
                           scratch_dir=tmp_path, backend=backend)
    assert result.tier == "high"
    assert result.upgraded_from_rule is True


def test_classify_risk_rejects_llm_downgrade(tmp_path):
    """Rule says 'high'; LLM tries to return 'low'. Result MUST stay at the rule floor."""
    rules = RuleSet(default_tier="medium", rules=[
        RiskRule(pattern="production", tier="high", reason="prod"),
    ])
    spec = OpsSpec(
        title="x", rationale="x", target="x", action="x",
        preconditions=[], postconditions=[], rollback="x",
        dry_run_supported=True, estimated_blast_radius="x", external_calls=[],
    )
    llm_json = json.dumps({"tier": "low", "reason": "trying to downgrade",
                            "upgraded_from_rule": False})
    backend = _stub_backend(llm_json)
    result = classify_risk("deploy to production", spec, rules=rules,
                           scratch_dir=tmp_path, backend=backend)
    # Rule floor is "high" — LLM cannot lower to "low"
    assert result.tier == "high"


# ---- 3. dry_run_action ----

def test_dry_run_action_parses_result(tmp_path):
    spec = OpsSpec(
        title="x", rationale="x", target="/tmp/x", action="x",
        preconditions=[], postconditions=[], rollback="x",
        dry_run_supported=True, estimated_blast_radius="x", external_calls=[],
    )
    dr_json = json.dumps({
        "mode": "dry_run",
        "would_do": ["read /tmp/x", "rewrite with new content"],
        "expected_blast_radius": "scoped to /tmp/x",
        "concerns": [],
    })
    backend = _stub_backend(dr_json)
    res = dry_run_action(spec, tier="medium", scratch_dir=tmp_path, backend=backend)
    assert res.mode == "dry_run"
    assert len(res.would_do) == 2
    assert res.concerns == []


# ---- 4. execute_action ----

def test_execute_action_parses_result(tmp_path):
    spec = OpsSpec(
        title="x", rationale="x", target="/tmp/x", action="x",
        preconditions=[], postconditions=[], rollback="x",
        dry_run_supported=True, estimated_blast_radius="x", external_calls=[],
    )
    ex_json = json.dumps({
        "mode": "execute",
        "actions_taken": ["wrote new token to /tmp/x"],
        "errors": [],
        "state_changes_observed": ["file size went from 32 to 48 bytes"],
    })
    backend = _stub_backend(ex_json)
    res = execute_action(spec, tier="low", scratch_dir=tmp_path, backend=backend)
    assert res.mode == "execute"
    assert res.errors == []
    assert len(res.actions_taken) == 1


# ---- 5. validate_action ----

def test_validate_action_passes_when_postconditions_hold(tmp_path):
    spec = OpsSpec(
        title="x", rationale="x", target="/tmp/x", action="x",
        preconditions=[], postconditions=["file content is new"],
        rollback="x",
        dry_run_supported=True, estimated_blast_radius="x", external_calls=[],
    )
    before = SnapshotBundle(target="/tmp/x", kind="file", hash_value="old_hash",
                             content_repr="old content", captured_at="2026-05-15")
    after = SnapshotBundle(target="/tmp/x", kind="file", hash_value="new_hash",
                            content_repr="new content", captured_at="2026-05-15")
    val_json = json.dumps({
        "passed": True, "summary": "postcondition verified",
        "postcondition_results": [{
            "postcondition": "file content is new",
            "verified": True,
            "evidence": "after-snapshot shows new content",
            "severity_if_failed": "important",
        }],
    })
    backend = _stub_backend(val_json)
    result = validate_action(spec, before, after, scratch_dir=tmp_path, backend=backend)
    assert result.passed is True


def test_validate_action_blocks_on_unverified_critical(tmp_path):
    spec = OpsSpec(
        title="x", rationale="x", target="/tmp/x", action="x",
        preconditions=[], postconditions=["file content is new"],
        rollback="x",
        dry_run_supported=True, estimated_blast_radius="x", external_calls=[],
    )
    before = SnapshotBundle(target="/tmp/x", kind="file", hash_value="h",
                             content_repr="", captured_at="2026-05-15")
    after = SnapshotBundle(target="/tmp/x", kind="file", hash_value="h",
                            content_repr="", captured_at="2026-05-15")
    val_json = json.dumps({
        "passed": False, "summary": "postcondition NOT verified",
        "postcondition_results": [{
            "postcondition": "file content is new",
            "verified": False,
            "evidence": "snapshots are identical — no change happened",
            "severity_if_failed": "critical",
        }],
    })
    backend = _stub_backend(val_json)
    result = validate_action(spec, before, after, scratch_dir=tmp_path, backend=backend)
    assert result.passed is False
    assert any(p.severity_if_failed == "critical" and not p.verified
               for p in result.postcondition_results)
