"""Risk-tier classifier for the ops workflow.

Declarative rules in workflows/ops/risk-rules.yaml. Pattern matching is
case-insensitive regex over the intent text. Highest-matching tier wins.

Tiers (low → critical) form a one-way escalation: the LLM-based classifier
can raise the rule-based floor but never lower it. The router enforces.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml


RiskTier = Literal["low", "medium", "high", "critical"]


TIER_ORDER: list[str] = ["low", "medium", "high", "critical"]


def max_tier(a: str, b: str) -> str:
    """Return whichever tier is higher; ties return either."""
    try:
        ai = TIER_ORDER.index(a)
        bi = TIER_ORDER.index(b)
    except ValueError:
        return a
    return a if ai >= bi else b


@dataclass(frozen=True)
class RiskRule:
    pattern: str
    tier: str
    reason: str


@dataclass(frozen=True)
class RuleSet:
    default_tier: str
    rules: list[RiskRule]


@dataclass(frozen=True)
class RiskClassification:
    tier: str
    reason: str
    matched_pattern: Optional[str] = None


def load_rules(path: Path) -> RuleSet:
    if not path.exists():
        raise FileNotFoundError(f"risk rules not found: {path}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"risk rules must be a YAML dict: {path}")
    default_tier = str(raw.get("default_tier", "medium"))
    if default_tier not in TIER_ORDER:
        raise ValueError(f"invalid default_tier in {path}: {default_tier!r}")
    rules = []
    for r in (raw.get("rules") or []):
        if not isinstance(r, dict):
            continue
        try:
            rules.append(RiskRule(
                pattern=str(r["pattern"]),
                tier=str(r["tier"]),
                reason=str(r.get("reason", "")),
            ))
        except KeyError:
            continue
    return RuleSet(default_tier=default_tier, rules=rules)


def classify_intent_by_rules(
    intent: str,
    *,
    spec_target: str = "",
    rules: RuleSet,
) -> RiskClassification:
    """Pure rule-based classification. Returns the HIGHEST-tier matching rule
    (so a single intent that matches multiple rules gets the highest tier).
    If no rule matches, falls back to default_tier."""
    if not intent and not spec_target:
        return RiskClassification(
            tier=rules.default_tier,
            reason=f"empty intent — defaulting to {rules.default_tier}",
        )
    text = f"{intent} {spec_target}".lower()
    any_matched = False
    matched_tier: str = "low"  # start at lowest; first match sets it; subsequent matches max it
    matched_reason = f"no rule matched; default tier {rules.default_tier}"
    matched_pattern = None
    for rule in rules.rules:
        try:
            if re.search(rule.pattern, text, re.IGNORECASE):
                if not any_matched:
                    # First match — take this tier directly
                    any_matched = True
                    matched_tier = rule.tier
                    matched_reason = rule.reason
                    matched_pattern = rule.pattern
                else:
                    # Subsequent matches — only upgrade
                    if max_tier(rule.tier, matched_tier) == rule.tier and rule.tier != matched_tier:
                        matched_tier = rule.tier
                        matched_reason = rule.reason
                        matched_pattern = rule.pattern
        except re.error:
            continue
    if not any_matched:
        return RiskClassification(
            tier=rules.default_tier,
            reason=f"no rule matched; default tier {rules.default_tier}",
            matched_pattern=None,
        )
    return RiskClassification(
        tier=matched_tier,
        reason=matched_reason,
        matched_pattern=matched_pattern,
    )
