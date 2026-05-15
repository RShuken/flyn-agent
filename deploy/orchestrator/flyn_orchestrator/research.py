"""Research workflow orchestration helpers.

Pure functions (no TaskRouter dependencies). TaskRouter calls into these:
  1. decompose_intent → PMPlan
  2. run_researchers(plan) → list[ResearcherOutput] (parallel)
  3. critique(plan, outputs) → CritiqueResult
  4. synthesize(...) → Markdown report
  5. write_output(...) → Path to written report

Thread-safety note: run_researchers uses ThreadPoolExecutor. Each backend.run()
call opens its own Popen with its own pipes (ClaudePBackend), or writes to its
own scratch file (stub backends). There is no shared mutable state between
threads, so this is safe.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .backends.base import WorkerBackend
from .citations import (
    Citation, ResearcherOutput, _extract_json_block,
    parse_researcher_output, validate_citations,
)
from .types import WorkerSpec, WorkerRole


_PROMPTS_DIR = Path(__file__).parent / "prompts"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PMPlan:
    title: str
    rationale: str
    sub_questions: list[dict]    # [{"id": "Q1", "question": "..."}]
    estimated_sources: str


@dataclass(frozen=True)
class CritiqueFinding:
    severity: str        # info | minor | important | critical
    category: str        # unsourced | contradiction | bias | citation_hygiene | gap
    note: str
    sub_question_id: Optional[str] = None


@dataclass(frozen=True)
class CritiqueResult:
    passed: bool
    summary: str
    findings: list[CritiqueFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    """Read prompts/<name>.md from the module's prompts directory."""
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def _extract_result_text(capture_path: Path) -> Optional[str]:
    """Pull the last 'result' event's text from a stream-json capture file.

    The capture file is JSONL (one JSON object per line). We scan from the
    bottom to find the last event whose type=='result' and return its text.
    """
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


def _slugify(text: str, max_len: int = 64) -> str:
    """Convert a title to a filesystem-safe path component."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] if s else "untitled"


# ---------------------------------------------------------------------------
# 1. Decompose intent
# ---------------------------------------------------------------------------

def decompose_intent(
    intent: str,
    *,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "research-decompose",
) -> Optional[PMPlan]:
    """Run PM-role worker; parse its JSON output into a PMPlan.

    Returns None if the PM emits unparseable output or is missing required fields.
    """
    prompt = _load_prompt("pm_research").replace("{INTENT}", intent)
    spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-pm",
        role=WorkerRole.PM,
        backend=backend.name,
        prompt_template="pm_research",
        worktree_path=str(scratch_dir),
        max_turns=3,
        budget_usd=0.50,
        readonly=True,
        allowed_tools=["Read"],
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
    required = {"title", "rationale", "sub_questions", "estimated_sources"}
    if not required.issubset(d.keys()):
        return None
    sqs = d.get("sub_questions") or []
    if not isinstance(sqs, list):
        return None
    return PMPlan(
        title=str(d["title"]),
        rationale=str(d["rationale"]),
        sub_questions=[
            {"id": str(q.get("id", "")), "question": str(q.get("question", ""))}
            for q in sqs[:4]   # cap at 4 per spec
        ],
        estimated_sources=str(d["estimated_sources"]),
    )


# ---------------------------------------------------------------------------
# 2. Run researchers (parallel)
# ---------------------------------------------------------------------------

def run_researchers(
    plan: PMPlan,
    *,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "research",
    max_parallel: int = 4,
) -> list[ResearcherOutput]:
    """Spawn one researcher per sub-question (up to max_parallel). Concurrent.

    Each researcher gets its own worktree subdirectory. Results are sorted by
    sub_question_id for deterministic downstream behavior.

    Thread-safety: backend.run() is called from multiple threads. Each call
    writes to its own scratch directory and capture path — no shared mutable
    state. ClaudePBackend.run() opens its own Popen per call; the stub backend
    in tests writes to per-worker files. Both are thread-safe.
    """
    sub_qs = plan.sub_questions[:max_parallel]
    if not sub_qs:
        return []
    template = _load_prompt("researcher")

    def _run_one(sub_q: dict) -> Optional[ResearcherOutput]:
        worker_dir = scratch_dir / sub_q["id"]
        worker_dir.mkdir(parents=True, exist_ok=True)
        prompt = (
            template
            .replace("{SUB_QUESTION}", sub_q["question"])
            .replace("{RESEARCH_TITLE}", plan.title)
        )
        spec = WorkerSpec(
            task_id=task_id,
            worker_id=f"{task_id}-researcher-{sub_q['id']}",
            role=WorkerRole.RESEARCHER,
            backend=backend.name,
            prompt_template="researcher",
            worktree_path=str(worker_dir),
            max_turns=8,
            budget_usd=0.50,
            readonly=False,    # researchers may write scratch notes
            allowed_tools=["Read", "WebFetch", "WebSearch", "Write"],
        )
        res = backend.run(spec, prompt)
        text = res.summary or _extract_result_text(res.capture_path) or ""
        return parse_researcher_output(text)

    outputs: list[ResearcherOutput] = []
    workers = min(len(sub_qs), max_parallel)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_one, sq) for sq in sub_qs]
        for fut in as_completed(futures):
            r = fut.result()
            if r is not None:
                outputs.append(r)

    # Sort by sub_question_id for deterministic downstream behavior
    outputs.sort(key=lambda o: o.sub_question_id)
    return outputs


# ---------------------------------------------------------------------------
# 3. Critique
# ---------------------------------------------------------------------------

def critique(
    plan: PMPlan,
    outputs: list[ResearcherOutput],
    *,
    scratch_dir: Path,
    backend: WorkerBackend,
    task_id: str = "research-critique",
) -> CritiqueResult:
    """Run critic role on combined researcher output.

    Pre-flight: runs validate_citations() on each output's citation list and
    merges any auto-findings into the critic's findings as severity=minor,
    category=citation_hygiene. These do NOT block on their own, but are visible
    to the critic and passed through to the synthesizer.
    """
    # Pre-flight citation validation
    auto_findings: list[CritiqueFinding] = []
    for o in outputs:
        for issue in validate_citations(o.citations):
            auto_findings.append(CritiqueFinding(
                severity="minor",
                category="citation_hygiene",
                note=f"[{o.sub_question_id}] {issue}",
                sub_question_id=o.sub_question_id,
            ))

    sq_text = json.dumps(
        [{"id": q["id"], "question": q["question"]} for q in plan.sub_questions],
        indent=2,
    )
    out_text = json.dumps(
        [
            {
                "sub_question_id": o.sub_question_id,
                "sub_question": o.sub_question,
                "answer": o.answer,
                "citations": [
                    {"url": c.url, "title": c.title, "claim": c.claim,
                     "accessed_at": c.accessed_at}
                    for c in o.citations
                ],
                "confidence": o.confidence,
                "open_questions": o.open_questions,
            }
            for o in outputs
        ],
        indent=2,
    )
    prompt = (
        _load_prompt("critic")
        .replace("{SUB_QUESTIONS}", sq_text)
        .replace("{RESEARCHER_OUTPUTS}", out_text)
    )
    spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-critic",
        role=WorkerRole.CRITIC,
        backend=backend.name,
        prompt_template="critic",
        worktree_path=str(scratch_dir),
        max_turns=3,
        budget_usd=0.30,
        readonly=True,
        allowed_tools=["Read"],
    )
    result = backend.run(spec, prompt)
    text = result.summary or _extract_result_text(result.capture_path) or ""
    block = _extract_json_block(text)
    if not block:
        return CritiqueResult(
            passed=False,
            summary="critic output unparseable",
            findings=auto_findings + [
                CritiqueFinding(
                    severity="critical",
                    category="gap",
                    note="critic emitted no parseable JSON; treat as block",
                )
            ],
        )
    try:
        d = json.loads(block)
    except json.JSONDecodeError:
        return CritiqueResult(
            passed=False,
            summary="bad json from critic",
            findings=auto_findings,
        )
    critic_findings = [
        CritiqueFinding(
            severity=str(f.get("severity", "info")),
            category=str(f.get("category", "gap")),
            note=str(f.get("note", "")),
            sub_question_id=f.get("sub_question_id"),
        )
        for f in (d.get("findings") or [])
    ]
    all_findings = auto_findings + critic_findings
    has_blocker = any(f.severity in ("critical", "important") for f in all_findings)
    return CritiqueResult(
        passed=bool(d.get("passed", False)) and not has_blocker,
        summary=str(d.get("summary", "")),
        findings=all_findings,
    )


# ---------------------------------------------------------------------------
# 4. Synthesize
# ---------------------------------------------------------------------------

def synthesize(
    *,
    title: str,
    requester: str,
    task_id: str,
    rationale: str,
    outputs: list[ResearcherOutput],
    minor_findings: list[CritiqueFinding],
    scratch_dir: Path,
    backend: WorkerBackend,
) -> str:
    """Merge researcher outputs into a single Markdown report.

    Returns the raw text returned by the synthesizer worker (Markdown).
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_text = json.dumps(
        [
            {
                "sub_question_id": o.sub_question_id,
                "sub_question": o.sub_question,
                "answer": o.answer,
                "citations": [
                    {"url": c.url, "title": c.title, "claim": c.claim,
                     "accessed_at": c.accessed_at}
                    for c in o.citations
                ],
                "confidence": o.confidence,
                "open_questions": o.open_questions,
            }
            for o in outputs
        ],
        indent=2,
    )
    minor_text = (
        "\n".join(f"- {f.category}/{f.severity}: {f.note}" for f in minor_findings)
        or "(none)"
    )
    prompt = (
        _load_prompt("synthesizer")
        .replace("{TITLE}", title)
        .replace("{DATE}", date)
        .replace("{REQUESTER}", requester)
        .replace("{TASK_ID}", task_id)
        .replace("{RATIONALE}", rationale)
        .replace("{RESEARCHER_OUTPUTS}", out_text)
        .replace("{CRITIC_MINOR_FINDINGS}", minor_text)
    )
    spec = WorkerSpec(
        task_id=task_id,
        worker_id=f"{task_id}-synthesizer",
        role=WorkerRole.SYNTHESIZER,
        backend=backend.name,
        prompt_template="synthesizer",
        worktree_path=str(scratch_dir),
        max_turns=5,
        budget_usd=0.50,
        readonly=True,
        allowed_tools=["Read"],
    )
    result = backend.run(spec, prompt)
    return result.summary or _extract_result_text(result.capture_path) or ""


# ---------------------------------------------------------------------------
# 5. Write output
# ---------------------------------------------------------------------------

def write_output(
    *,
    report_md: str,
    outputs: list[ResearcherOutput],
    title: str,
    task_id: str,
) -> Path:
    """Write the Markdown report + raw notes JSON files. Returns the report path.

    Output root: $FLYN_RESEARCH_OUTPUT_ROOT or ~/Work/research/ (fallback).
    Layout:
        <root>/<topic-slug>/<date>-<topic-slug>.md          ← final report
        <root>/<topic-slug>/raw/<date>-<task_id>-Q<n>.json  ← per-researcher notes
    """
    root = Path(
        os.environ.get(
            "FLYN_RESEARCH_OUTPUT_ROOT",
            str(Path.home() / "Work" / "research"),
        )
    )
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topic_slug = _slugify(title)
    topic_dir = root / topic_slug
    raw_dir = topic_dir / "raw"
    topic_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    report_path = topic_dir / f"{date}-{topic_slug}.md"
    report_path.write_text(report_md)

    for o in outputs:
        raw_path = raw_dir / f"{date}-{task_id}-{o.sub_question_id}.json"
        raw_path.write_text(
            json.dumps(
                {
                    "sub_question_id": o.sub_question_id,
                    "sub_question": o.sub_question,
                    "answer": o.answer,
                    "citations": [
                        {"url": c.url, "title": c.title, "claim": c.claim,
                         "accessed_at": c.accessed_at}
                        for c in o.citations
                    ],
                    "confidence": o.confidence,
                    "open_questions": o.open_questions,
                },
                indent=2,
            )
        )

    return report_path
