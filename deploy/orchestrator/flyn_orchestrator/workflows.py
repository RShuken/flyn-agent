"""Workflow loader + intent matcher.

A workflow is a YAML policy file declaring:
- name (matches the workflow=<name> field on TaskRecord)
- intent_patterns (lowercase substrings matched against the inbound intent)
- roles (worker roles with model + prompt template name + optional parallel/readonly flags)
- flow (ordered list of state machine phases)
- approval_gates (which roles can authorize each gate)
- budget_default_usd (per-task budget cap, overridable per-request)

Loaded from disk at orchestrator startup. The router matches an inbound
task's intent against each loaded workflow's intent_patterns; first match wins.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


class WorkflowNotFound(FileNotFoundError):
    """Raised when load_workflow can't find the file."""


@dataclass(frozen=True)
class Role:
    name: str
    model: str = "claude"          # claude | codex
    prompt: str = ""               # filename stem under prompts/
    parallel: bool = False         # multiple instances allowed concurrently?
    readonly: bool = False         # cannot edit files (for reviewer)


@dataclass(frozen=True)
class Workflow:
    name: str
    intent_patterns: tuple[str, ...]
    roles: tuple[Role, ...]
    flow: tuple[str, ...]
    approval_gates: dict[str, str]      # gate_name -> required role tier
    budget_default_usd: float

    def get_role(self, name: str) -> Optional[Role]:
        for r in self.roles:
            if r.name == name:
                return r
        return None


def load_workflow(path: Path) -> Workflow:
    if not path.exists():
        raise WorkflowNotFound(f"workflow file not found: {path}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"workflow file is not a dict: {path}")
    roles = tuple(
        Role(
            name=r["name"],
            model=r.get("model", "claude"),
            prompt=r.get("prompt", ""),
            parallel=bool(r.get("parallel", False)),
            readonly=bool(r.get("readonly", False)),
        )
        for r in (raw.get("roles") or [])
    )
    return Workflow(
        name=raw["name"],
        intent_patterns=tuple(raw.get("intent_patterns") or []),
        roles=roles,
        flow=tuple(raw.get("flow") or []),
        approval_gates=dict(raw.get("approval_gates") or {}),
        budget_default_usd=float(raw.get("budget_default_usd", 5.0)),
    )


def load_workflows_dir(dir_path: Path) -> list[Workflow]:
    """Load every *.yaml under dir_path. Sort by name for deterministic order."""
    if not dir_path.exists():
        return []
    out = []
    for p in sorted(dir_path.glob("*.yaml")):
        try:
            out.append(load_workflow(p))
        except Exception:
            # Skip malformed files but log via stderr; orchestrator must keep starting.
            import sys
            print(f"warning: failed to load workflow {p}: skipping", file=sys.stderr)
    return out


def match_intent(intent: str, workflows: list[Workflow]) -> Optional[Workflow]:
    """Return the first workflow whose intent_patterns matches the intent.

    Match is case-insensitive whole-word/substring. Use word-boundary regex when
    the pattern contains no spaces, plain substring when it does.
    """
    if not intent:
        return None
    text = intent.lower()
    for wf in workflows:
        for pat in wf.intent_patterns:
            patt = pat.lower()
            if " " in patt:
                if patt in text:
                    return wf
            else:
                if re.search(rf"\b{re.escape(patt)}\b", text):
                    return wf
    return None
