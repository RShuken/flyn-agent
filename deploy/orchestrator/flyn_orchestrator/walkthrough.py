"""Walk-me-through-PRs: generates non-technical English explanations of a PR diff.

Uses a fresh-context claude -p invocation (separate from the reviewer). Prompted
explicitly for plain-English output for non-technical reviewers (Beth, Eric).

API: generate_walkthrough(pr_url, diff, task_intent, backend=None) -> str
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from typing import Optional

from .backends import default_registry
from .backends.base import WorkerBackend
from .config import Config
from .types import WorkerSpec, WorkerRole


_PROMPT_PATH = Path(__file__).parent / "prompts" / "walkthrough.md"


def _render_prompt(*, pr_url: str, diff: str, task_intent: str) -> str:
    tmpl = _PROMPT_PATH.read_text()
    return (
        tmpl.replace("{PR_URL}", pr_url)
            .replace("{DIFF}", diff)
            .replace("{TASK_INTENT}", task_intent)
    )


def _extract_text_from_capture(capture_text: str) -> Optional[str]:
    """Pull the assistant's text from the last 'result' event in stream-json output."""
    for line in reversed(capture_text.strip().splitlines()):
        try:
            ev = json.loads(line)
            if ev.get("type") == "result":
                res = ev.get("result")
                if isinstance(res, str):
                    return res
                if isinstance(res, dict) and "summary" in res:
                    return str(res["summary"])
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def generate_walkthrough(*, pr_url: str, diff: str, task_intent: str,
                         backend: Optional[WorkerBackend] = None,
                         backend_name: Optional[str] = None) -> str:
    if backend_name is None:
        backend_name = Config.from_env().default_backend
    backend = backend or default_registry().get(backend_name)
    prompt = _render_prompt(pr_url=pr_url, diff=diff, task_intent=task_intent)
    # Use a scratch tempdir for the worker invocation — no worktree required for read-only walkthrough
    with tempfile.TemporaryDirectory() as scratch:
        spec = WorkerSpec(
            task_id="walkthrough", worker_id="walkthrough-" + str(abs(hash(pr_url)) % 10000),
            role=WorkerRole.REVIEWER,  # READ-ONLY
            backend=backend_name, prompt_template="walkthrough",
            worktree_path=scratch, max_turns=3, budget_usd=0.50,
            readonly=True,
            allowed_tools=["Read"],
        )
        res = backend.run(spec, prompt)
        # Try summary first
        if res.summary:
            text = res.summary
        elif res.capture_path.exists():
            text = _extract_text_from_capture(res.capture_path.read_text()) or ""
        else:
            text = ""
    if not text.strip():
        return "(walkthrough generation failed — no output from worker)"
    return text.strip()
