# tests/unit/test_risk_tier.py
from pathlib import Path
import pytest
import yaml
from flyn_orchestrator.risk_tier import (
    classify_intent_by_rules, RiskTier, RiskClassification,
    load_rules, TIER_ORDER, max_tier,
)


def _write_rules(tmp_path, rules):
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.safe_dump({"default_tier": "medium", "rules": rules}))
    return p


def test_low_tier_matches_test_token(tmp_path):
    p = _write_rules(tmp_path, [
        {"pattern": "test.*token", "tier": "low", "reason": "test"},
    ])
    rules = load_rules(p)
    res = classify_intent_by_rules("rotate the test token", spec_target="", rules=rules)
    assert res.tier == "low"


def test_default_tier_when_no_rule_matches(tmp_path):
    p = _write_rules(tmp_path, [])
    rules = load_rules(p)
    res = classify_intent_by_rules("do something unusual", spec_target="", rules=rules)
    assert res.tier == "medium"
    assert "default" in res.reason.lower() or "no rule" in res.reason.lower()


def test_highest_tier_wins(tmp_path):
    """If multiple rules match, the highest tier wins."""
    p = _write_rules(tmp_path, [
        {"pattern": "rotate", "tier": "low", "reason": "rotation"},
        {"pattern": "production", "tier": "high", "reason": "prod"},
        {"pattern": "rotate.*production", "tier": "high", "reason": "prod rotate"},
    ])
    rules = load_rules(p)
    res = classify_intent_by_rules("rotate production API key",
                                    spec_target="", rules=rules)
    assert res.tier == "high"


def test_critical_rules_match_destructive(tmp_path):
    p = _write_rules(tmp_path, [
        {"pattern": "delete|wipe|drop.*database", "tier": "critical",
         "reason": "destructive"},
    ])
    rules = load_rules(p)
    assert classify_intent_by_rules("delete all users", spec_target="", rules=rules).tier == "critical"
    assert classify_intent_by_rules("wipe the db", spec_target="", rules=rules).tier == "critical"
    assert classify_intent_by_rules("drop the database tables", spec_target="", rules=rules).tier == "critical"


def test_tier_order_low_to_critical():
    assert TIER_ORDER == ["low", "medium", "high", "critical"]


def test_max_tier_returns_higher():
    assert max_tier("low", "medium") == "medium"
    assert max_tier("high", "critical") == "critical"
    assert max_tier("medium", "medium") == "medium"
    assert max_tier("critical", "low") == "critical"


def test_classification_includes_reason(tmp_path):
    p = _write_rules(tmp_path, [
        {"pattern": "rotate.*production", "tier": "high",
         "reason": "production credential rotation"},
    ])
    rules = load_rules(p)
    res = classify_intent_by_rules("rotate production stripe key",
                                    spec_target="", rules=rules)
    assert res.tier == "high"
    assert "production credential" in res.reason.lower()


def test_real_rules_file_loads_cleanly():
    """Verify the real risk-rules.yaml loads without errors."""
    real_rules_path = (
        Path(__file__).parents[2] / "flyn_orchestrator" / "workflows" / "ops"
        / "risk-rules.yaml"
    )
    rules = load_rules(real_rules_path)
    # Real file should have multiple rules
    assert len(rules.rules) >= 10
    # Test a low-tier classification against the real rules
    res = classify_intent_by_rules("rotate the test token", spec_target="",
                                    rules=rules)
    assert res.tier == "low"
