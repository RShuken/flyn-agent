"""Ops workflow orchestration helpers. Five pure functions.

Each function takes backend: WorkerBackend (testable end-to-end with stubs).

Functions:
  spec_ops_action   — PM role specs the ops action + postconditions
  classify_risk     — rule-based floor + LLM upgrade-only classification
  dry_run_action    — executor in dry_run mode (no state changes)
  execute_action    — executor in execute mode (real state changes)
  validate_action   — validator fresh-context post-condition checker

One-way escalation: the LLM classifier can RAISE the rule floor but
never lower it. max_tier() from risk_tier enforces this invariant.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from .audit import SnapshotBundle, serialize_snapshot
from .backends.base import WorkerBackend
from .citations import _extract_json_block
from .risk_tier import RuleSet, RiskClassification, classify_intent_by_rules, max_tier
from .types import WorkerSpec, WorkerRole


_PROMPTS_DIR = Path(__file__).parent / "prompts"


# ---------- Dataclasses ----------

@dataclass(frozen=True)
class OpsSpec:
    title: str
    rationale: str
    target: str
    action: str
    preconditions: list[str]
    postconditions: list[str]
    rollback: str
    dry_run_supported: bool
    estimated_blast_radius: str
    external_calls: list[str]


@dataclass(frozen=True)
class RiskAssessment:
    tier: str                   # low | medium | high | critical
    reason: str
    upgraded_from_rule: bool
    rule_floor: str
    matched_pattern: Optional[str] = None


@dataclass(frozen=True)
class DryRunResult:
    mode: Literal["dry_run"]
    would_do: list[str]
    expected_blast_radius: str
    concerns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecuteResult:
    mode: Literal["execute"]
    actions_taken: list[str]
    errors: list[str]
    state_changes_observed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PostConditionResult:
    postcondition: str
    verified: bool
    evidence: str
    severity_if_failed: str


@dataclass(frozen=True)
class ValidatorResult:
    passed: bool
    summary: str
    postcondition_results: list[PostConditionResult] = field(default_factory=list)


# ---------- Internal helpers ----------

def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def _extract_result_text(capture_path: Path) -> Optional[str]:
    """Extract the summary/result text from a worker JSONL capture file."""
    if not capture_path or not capture_path.exists():
        return None
    text = capture_path.read_text()
    for line in reversed(text.strip().splitlines()):
        try:
            ev = json.loads(line)
            if ev.get("type") == "result":
                res = ev.get("result")
                if isinstance(res, str):
                    return res
                if isinstance(res, dict):
                    return res.get("summary") or json.dumps(res)
        except json.JSONDecodeError:
            continue
    return None


def _spec_to_json(spec: OpsSpec) -> str:
    """Serialize OpsSpec to JSON for prompt injection."""
    return json.dumps({
        "title": spec.title,
        "rationale": spec.rationale,
        "target": spec.target,
        "action": spec.action,
        "preconditions": spec.preconditions,
        "postconditions": spec.postconditions,
        "rollback": spec.rollback,
        "dry_run_supported": spec.dry_run_supported,
        "estimated_blast_radius": spec.estimated_blast_radius,
        "external_calls": spec.external_calls,
    }, indent=2)


# ---------- 1. PM specs the action ----------

def spec_ops_action(
    intent: str,
    *,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "ops-spec",
) -> Optional[OpsSpec]:
    """Ask the PM role to produce a structured OpsSpec from the user's intent.

    Returns None if the backend output is unparseable or missing required fields.
    The PM prompt instructs the model to set title="(ambiguous)" if the intent
    is too vague; callers should check for that sentinel.
    """
    prompt = _load_prompt("pm_ops").replace("{INTENT}", intent)
    worker_spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-pm",
        role=WorkerRole.PM,
        backend=backend.name,
        prompt_template="pm_ops",
        worktree_path=str(scratch_dir),
        max_turns=3,
        budget_usd=0.30,
        readonly=True,
        allowed_tools=["Read"],
    )
    result = backend.run(worker_spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return None
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return None
    required = {
        "title", "rationale", "target", "action", "preconditions",
        "postconditions", "rollback", "dry_run_supported",
        "estimated_blast_radius",
    }
    if not required.issubset(d.keys()):
        return None
    return OpsSpec(
        title=str(d["title"]),
        rationale=str(d["rationale"]),
        target=str(d["target"]),
        action=str(d["action"]),
        preconditions=list(d.get("preconditions") or []),
        postconditions=list(d.get("postconditions") or []),
        rollback=str(d["rollback"]),
        dry_run_supported=bool(d.get("dry_run_supported", False)),
        estimated_blast_radius=str(d.get("estimated_blast_radius", "")),
        external_calls=list(d.get("external_calls") or []),
    )


# ---------- 2. Risk classify (rule-based floor + LLM upgrade only) ----------

def classify_risk(
    intent: str,
    ops_spec: OpsSpec,
    *,
    rules: RuleSet,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "ops-risk",
) -> RiskAssessment:
    """Classify the risk tier using rule-based floor + LLM augmentation.

    One-way escalation invariant: the LLM may raise the rule floor but
    NEVER lower it. max_tier(llm_tier, rule_floor) is the final arbiter.
    """
    # Step 1 — deterministic rule-based floor
    rule_result = classify_intent_by_rules(
        intent,
        spec_target=ops_spec.target,
        rules=rules,
    )

    # Step 2 — LLM considers whether to upgrade (upgrade only)
    prompt = (
        _load_prompt("risk_classifier")
        .replace("{SPEC_JSON}", _spec_to_json(ops_spec))
        .replace("{RULE_TIER}", rule_result.tier)
        .replace("{RULE_REASON}", rule_result.reason)
    )
    worker_spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-classifier",
        role=WorkerRole.CRITIC,     # readonly role — closest existing fit
        backend=backend.name,
        prompt_template="risk_classifier",
        worktree_path=str(scratch_dir),
        max_turns=2,
        budget_usd=0.20,
        readonly=True,
        allowed_tools=["Read"],
    )
    result = backend.run(worker_spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)

    # Parse LLM output; fall back to rule tier on any parse error
    llm_tier = rule_result.tier
    llm_reason = rule_result.reason

    if block:
        try:
            d = json.loads(block)
            llm_tier = str(d.get("tier", rule_result.tier))
            llm_reason = str(d.get("reason", rule_result.reason))
        except json.JSONDecodeError:
            pass  # stay at rule floor

    # Step 3 — enforce one-way escalation: never lower the rule floor
    final_tier = max_tier(llm_tier, rule_result.tier)

    if final_tier == rule_result.tier:
        # LLM agreed or tried to downgrade — use rule reason; no upgrade
        upgraded = False
        final_reason = rule_result.reason
    else:
        # LLM raised the floor — use LLM reason; mark as upgraded
        upgraded = True
        final_reason = llm_reason

    return RiskAssessment(
        tier=final_tier,
        reason=final_reason,
        upgraded_from_rule=upgraded,
        rule_floor=rule_result.tier,
        matched_pattern=rule_result.matched_pattern,
    )


# ---------- 3. Dry run ----------

def dry_run_action(
    ops_spec: OpsSpec,
    *,
    tier: str,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "ops-dry-run",
) -> DryRunResult:
    """Run the executor in dry_run mode — describe what WOULD happen, no mutations."""
    prompt = (
        _load_prompt("executor")
        .replace("{SPEC_JSON}", _spec_to_json(ops_spec))
        .replace("{TIER}", tier)
        .replace("{MODE}", "dry_run")
    )
    worker_spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-executor-dry",
        role=WorkerRole.EXECUTOR,
        backend=backend.name,
        prompt_template="executor",
        worktree_path=str(scratch_dir),
        max_turns=4,
        budget_usd=0.30,
        readonly=True,
        allowed_tools=["Read", "Bash"],   # Bash for read-only inspection
    )
    result = backend.run(worker_spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return DryRunResult(
            mode="dry_run",
            would_do=[],
            expected_blast_radius="(unparseable)",
            concerns=["dry-run output unparseable; treat as block"],
        )
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return DryRunResult(
            mode="dry_run", would_do=[], expected_blast_radius="",
            concerns=["bad json in dry-run output"],
        )
    return DryRunResult(
        mode="dry_run",
        would_do=list(d.get("would_do") or []),
        expected_blast_radius=str(d.get("expected_blast_radius", "")),
        concerns=list(d.get("concerns") or []),
    )


# ---------- 4. Execute ----------

def execute_action(
    ops_spec: OpsSpec,
    *,
    tier: str,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "ops-execute",
) -> ExecuteResult:
    """Run the executor in execute mode — real state changes permitted."""
    prompt = (
        _load_prompt("executor")
        .replace("{SPEC_JSON}", _spec_to_json(ops_spec))
        .replace("{TIER}", tier)
        .replace("{MODE}", "execute")
    )
    worker_spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-executor",
        role=WorkerRole.EXECUTOR,
        backend=backend.name,
        prompt_template="executor",
        worktree_path=str(scratch_dir),
        max_turns=6,
        budget_usd=0.50,
        readonly=False,
        allowed_tools=["Read", "Write", "Edit", "Bash"],
    )
    result = backend.run(worker_spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return ExecuteResult(
            mode="execute",
            actions_taken=[],
            errors=["executor output unparseable"],
        )
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return ExecuteResult(
            mode="execute", actions_taken=[], errors=["bad json in execute output"],
        )
    return ExecuteResult(
        mode="execute",
        actions_taken=list(d.get("actions_taken") or []),
        errors=list(d.get("errors") or []),
        state_changes_observed=list(d.get("state_changes_observed") or []),
    )


# ---------- 5. Validate (fresh-context post-condition check) ----------

def validate_action(
    ops_spec: OpsSpec,
    before: SnapshotBundle,
    after: SnapshotBundle,
    *,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "ops-validate",
) -> ValidatorResult:
    """Validate that post-conditions hold by comparing before/after snapshots.

    The validator runs in a fresh context — it did NOT observe execution.
    Returns passed=False if ANY postcondition with severity critical or
    important is unverified.
    """
    prompt = (
        _load_prompt("validator")
        .replace("{SPEC_JSON}", _spec_to_json(ops_spec))
        .replace("{BEFORE_SNAPSHOT}", serialize_snapshot(before))
        .replace("{AFTER_SNAPSHOT}", serialize_snapshot(after))
    )
    worker_spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-validator",
        role=WorkerRole.VALIDATOR,
        backend=backend.name,
        prompt_template="validator",
        worktree_path=str(scratch_dir),
        max_turns=3,
        budget_usd=0.30,
        readonly=True,
        allowed_tools=["Read"],
    )
    result = backend.run(worker_spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return ValidatorResult(
            passed=False,
            summary="validator output unparseable",
            postcondition_results=[],
        )
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return ValidatorResult(
            passed=False, summary="bad json in validator output",
            postcondition_results=[],
        )
    pcs = [
        PostConditionResult(
            postcondition=str(p.get("postcondition", "")),
            verified=bool(p.get("verified", False)),
            evidence=str(p.get("evidence", "")),
            severity_if_failed=str(p.get("severity_if_failed", "info")),
        )
        for p in (d.get("postcondition_results") or [])
    ]
    # Block if any critical/important postcondition failed
    has_failing_blocker = any(
        not p.verified and p.severity_if_failed in ("critical", "important")
        for p in pcs
    )
    return ValidatorResult(
        passed=bool(d.get("passed", False)) and not has_failing_blocker,
        summary=str(d.get("summary", "")),
        postcondition_results=pcs,
    )
