"""Content workflow orchestration helpers. Five pure functions.

Each takes a `backend: WorkerBackend` parameter — testable end-to-end with
stub backends. Reuses the foundation pieces: WorkerSpec, WorkerRole, prompts/.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .backends.base import WorkerBackend
from .citations import _extract_json_block
from .types import WorkerSpec, WorkerRole


_PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class ContentSpec:
    title: str
    platform: str
    audience: str
    tone: str
    voice: str
    length_target: str
    key_points: list[str]
    needs_fact_check: bool
    needs_humanize: bool
    wants_send: bool
    send_destination: str


@dataclass(frozen=True)
class EditFinding:
    severity: str   # info | minor | important | critical
    type: str       # tone | clarity | length | spec_mismatch | typo | other
    where: str
    suggestion: str


@dataclass(frozen=True)
class EditResult:
    passed: bool
    summary: str
    edits: list[EditFinding] = field(default_factory=list)


@dataclass(frozen=True)
class FactCheckFinding:
    severity: str        # info | minor | important | critical
    claim: str
    issue: str           # wrong | unverified | outdated | opinion_as_fact | unsupported
    evidence: str
    suggestion: str


@dataclass(frozen=True)
class FactCheckResult:
    passed: bool
    summary: str
    claims_checked: int
    findings: list[FactCheckFinding] = field(default_factory=list)


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def _extract_result_text(capture_path: Path) -> Optional[str]:
    if not capture_path.exists():
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


def _spec_to_json(spec: ContentSpec) -> str:
    return json.dumps({
        "title": spec.title, "platform": spec.platform,
        "audience": spec.audience, "tone": spec.tone,
        "voice": spec.voice, "length_target": spec.length_target,
        "key_points": spec.key_points,
        "needs_fact_check": spec.needs_fact_check,
        "needs_humanize": spec.needs_humanize,
        "wants_send": spec.wants_send,
        "send_destination": spec.send_destination,
    }, indent=2)


# ---------- 1. Spec (PM) ----------

def spec_content(intent: str, *, scratch_dir: Path, backend: WorkerBackend,
                  task_id: str = "content-spec") -> Optional[ContentSpec]:
    """Run PM-role; return ContentSpec or None on unparseable output."""
    prompt = _load_prompt("pm_content").replace("{INTENT}", intent)
    spec = WorkerSpec(
        task_id=task_id, worker_id=f"{task_id}-pm", role=WorkerRole.PM,
        backend=backend.name, prompt_template="pm_content",
        worktree_path=str(scratch_dir), max_turns=3, budget_usd=0.20,
        readonly=True, allowed_tools=["Read"],
    )
    result = backend.run(spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return None
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return None
    required = {"title", "platform", "audience", "tone", "voice",
                 "length_target", "key_points", "needs_fact_check",
                 "needs_humanize", "wants_send"}
    if not required.issubset(d.keys()):
        return None
    return ContentSpec(
        title=str(d["title"]),
        platform=str(d["platform"]),
        audience=str(d["audience"]),
        tone=str(d["tone"]),
        voice=str(d["voice"]),
        length_target=str(d["length_target"]),
        key_points=list(d.get("key_points") or []),
        needs_fact_check=bool(d.get("needs_fact_check", False)),
        needs_humanize=bool(d.get("needs_humanize", False)),
        wants_send=bool(d.get("wants_send", False)),
        send_destination=str(d.get("send_destination", "")),
    )


# ---------- 2. Draft (Writer) ----------

def draft_content(content_spec: ContentSpec, *, scratch_dir: Path,
                   backend: WorkerBackend, task_id: str = "content-draft",
                   extra_context: Optional[str] = None) -> str:
    """Run Writer; return draft text.

    If *extra_context* is provided (Phase 4b auto-rerun path), it's appended
    to the writer's prompt with a `---` separator. This is how editor or
    fact-checker findings are fed back to the writer on retry.
    """
    prompt = (_load_prompt("writer")
              .replace("{SPEC_JSON}", _spec_to_json(content_spec)))
    if extra_context:
        prompt = prompt + "\n\n---\n\n" + extra_context
    spec = WorkerSpec(
        task_id=task_id, worker_id=f"{task_id}-writer", role=WorkerRole.WRITER,
        backend=backend.name, prompt_template="writer",
        worktree_path=str(scratch_dir), max_turns=4, budget_usd=0.30,
        readonly=True, allowed_tools=["Read"],
    )
    result = backend.run(spec, prompt)
    return result.summary or _extract_result_text(result.capture_path) or ""


# ---------- 3. Edit (Editor) ----------

def edit_content(content_spec: ContentSpec, draft: str, *, scratch_dir: Path,
                  backend: WorkerBackend, task_id: str = "content-edit") -> EditResult:
    """Run Editor (fresh-context); return EditResult with passed flag + list of edits."""
    prompt = (_load_prompt("editor")
              .replace("{SPEC_JSON}", _spec_to_json(content_spec))
              .replace("{DRAFT}", draft))
    spec = WorkerSpec(
        task_id=task_id, worker_id=f"{task_id}-editor", role=WorkerRole.EDITOR,
        backend=backend.name, prompt_template="editor",
        worktree_path=str(scratch_dir), max_turns=3, budget_usd=0.20,
        readonly=True, allowed_tools=["Read"],
    )
    result = backend.run(spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return EditResult(
            passed=False, summary="editor output unparseable",
            edits=[EditFinding(severity="critical", type="other",
                               where="", suggestion="editor emitted no parseable JSON")],
        )
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return EditResult(passed=False, summary="bad json from editor")
    edits = [
        EditFinding(
            severity=str(e.get("severity", "info")),
            type=str(e.get("type", "other")),
            where=str(e.get("where", "")),
            suggestion=str(e.get("suggestion", "")),
        )
        for e in (d.get("edits") or [])
    ]
    has_blocker = any(e.severity in ("critical", "important") for e in edits)
    return EditResult(
        passed=bool(d.get("passed", False)) and not has_blocker,
        summary=str(d.get("summary", "")),
        edits=edits,
    )


# ---------- 4. Fact-check (conditional) ----------

def fact_check_content(content_spec: ContentSpec, draft: str, *, scratch_dir: Path,
                        backend: WorkerBackend,
                        task_id: str = "content-fact-check") -> FactCheckResult:
    """Run Fact-checker; return FactCheckResult. Blocks on critical/important findings."""
    prompt = (_load_prompt("fact_checker")
              .replace("{SPEC_JSON}", _spec_to_json(content_spec))
              .replace("{DRAFT}", draft))
    spec = WorkerSpec(
        task_id=task_id, worker_id=f"{task_id}-fact-checker",
        role=WorkerRole.FACT_CHECKER,
        backend=backend.name, prompt_template="fact_checker",
        worktree_path=str(scratch_dir), max_turns=5, budget_usd=0.30,
        readonly=True, allowed_tools=["Read", "WebFetch", "WebSearch"],
    )
    result = backend.run(spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return FactCheckResult(
            passed=False, summary="fact-checker output unparseable",
            claims_checked=0,
            findings=[FactCheckFinding(severity="critical", claim="",
                                        issue="unverified", evidence="",
                                        suggestion="fact-checker emitted no parseable JSON")],
        )
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return FactCheckResult(passed=False, summary="bad json", claims_checked=0)
    findings = [
        FactCheckFinding(
            severity=str(f.get("severity", "info")),
            claim=str(f.get("claim", "")),
            issue=str(f.get("issue", "unverified")),
            evidence=str(f.get("evidence", "")),
            suggestion=str(f.get("suggestion", "")),
        )
        for f in (d.get("findings") or [])
    ]
    has_blocker = any(f.severity in ("critical", "important") for f in findings)
    return FactCheckResult(
        passed=bool(d.get("passed", False)) and not has_blocker,
        summary=str(d.get("summary", "")),
        claims_checked=int(d.get("claims_checked", 0)),
        findings=findings,
    )


# ---------- 5. Humanize (optional) ----------

def humanize_content(content_spec: ContentSpec, draft: str, *, scratch_dir: Path,
                      backend: WorkerBackend,
                      task_id: str = "content-humanize") -> str:
    """Run Humanizer-Invoker; return humanized draft text. Reuses WorkerRole.WRITER."""
    prompt = (_load_prompt("humanizer_invoker")
              .replace("{SPEC_JSON}", _spec_to_json(content_spec))
              .replace("{DRAFT}", draft))
    spec = WorkerSpec(
        task_id=task_id, worker_id=f"{task_id}-humanizer",
        role=WorkerRole.WRITER,    # reuse WRITER for humanizer — no HUMANIZER enum needed
        backend=backend.name, prompt_template="humanizer_invoker",
        worktree_path=str(scratch_dir), max_turns=3, budget_usd=0.20,
        readonly=True, allowed_tools=["Read"],
    )
    result = backend.run(spec, prompt)
    return result.summary or _extract_result_text(result.capture_path) or draft
