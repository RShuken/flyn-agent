# deploy/orchestrator/flyn_orchestrator/reviewer.py
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

from .backends import default_registry
from .backends.base import WorkerBackend
from .types import ReviewFindings, ReviewFinding, WorkerSpec, WorkerRole


_PROMPT_PATH = Path(__file__).parent / "prompts" / "reviewer.md"


def _render_prompt(requirements: str, diff: str, test_results: str) -> str:
    tmpl = _PROMPT_PATH.read_text()
    return (
        tmpl.replace("{REQUIREMENTS}", requirements)
            .replace("{DIFF}", diff)
            .replace("{TEST_RESULTS}", test_results)
    )


def _extract_json(text: str) -> Optional[dict]:
    """Find the first ```json fenced block or the first {...} JSON object."""
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # last-ditch: find first balanced top-level object
    try:
        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except json.JSONDecodeError:
        pass
    return None


def review(*, worker_id: str, requirements: str, diff: str, test_results: str,
           worktree_path: str, backend_name: str = "claude-p",
           backend: Optional[WorkerBackend] = None) -> ReviewFindings:
    # Short-circuit on empty diff: builder produced no output
    if not diff.strip():
        return ReviewFindings(
            worker_id=worker_id + "-reviewer",
            passed=False,
            summary="builder produced no diff",
            findings=[ReviewFinding(
                severity="critical", area="correctness",
                note="builder produced no diff — review skipped")])

    backend = backend or default_registry().get(backend_name)
    spec = WorkerSpec(
        task_id=worker_id, worker_id=worker_id + "-reviewer",
        role=WorkerRole.REVIEWER, backend=backend_name,
        prompt_template="reviewer", worktree_path=worktree_path,
        max_turns=4, budget_usd=1.0, readonly=True,
        allowed_tools=["Read", "Bash"],
    )
    prompt = _render_prompt(requirements, diff, test_results)
    res = backend.run(spec, prompt)
    # Pull review JSON out of the summary or, failing that, the capture
    obj = _extract_json(res.summary) if res.summary else None
    if obj is None and res.capture_path.exists():
        obj = _extract_json(res.capture_path.read_text())
    if obj is None:
        return ReviewFindings(worker_id=spec.worker_id, passed=False,
                              summary="reviewer did not emit parseable JSON",
                              findings=[ReviewFinding(
                                  severity="critical", area="correctness",
                                  note="reviewer output unparseable; treat as failed review")])
    findings = [ReviewFinding(**f) for f in obj.get("findings", [])]
    return ReviewFindings(
        worker_id=spec.worker_id,
        passed=bool(obj.get("passed", False)),
        summary=str(obj.get("summary", "")),
        findings=findings,
    )
