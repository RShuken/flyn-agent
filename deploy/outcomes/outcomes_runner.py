#!/usr/bin/env python3
"""Outcomes-driven iteration loop for Flyn's PM work.

Reads a phase rubric (markdown table of testable criteria), spawns a worker
agent to tackle unmet criteria, evaluates with a grader agent, iterates up
to N times.

Status: v0 scaffold. The Anthropic Managed Agents "Outcomes" API was announced
2026-05-06 and is in public beta. This script uses the regular Messages API
with a worker+grader pattern that mimics the Outcomes loop. When the official
Outcomes endpoint is stable on a per-account basis, swap _grade() to use it.

Usage:
  outcomes_runner.py --rubric path/to/PHASE-RUBRICS.md --phase 5 [--max-iter 5]

The runner:
  1. Parses the named phase out of the rubric markdown
  2. Identifies unmet criteria (rows starting with ⬜)
  3. Calls Claude as a worker with the criteria + project context
  4. Calls Claude as a grader with the rubric + worker output
  5. If grader passes all criteria → done; else feedback to worker, loop
  6. Writes per-run report to logs/<timestamp>-phase<N>.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Anthropic SDK is optional now; we fall back to `claude -p` CLI if it's missing
try:
    import anthropic  # noqa: F401  (kept available; call_claude uses it when API key exists)
except ImportError:
    pass


LOG_DIR = Path.home() / ".openclaw" / "logs" / "outcomes"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_api_key() -> str:
    """Pull from env first, then openclaw auth-profiles.json."""
    if v := os.environ.get("ANTHROPIC_API_KEY"):
        return v
    auth_path = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
    if auth_path.exists():
        try:
            d = json.loads(auth_path.read_text())
            return d["profiles"]["anthropic:default"]["token"]
        except (KeyError, json.JSONDecodeError):
            pass
    raise RuntimeError("No ANTHROPIC_API_KEY in env or auth-profiles.json")


# ---------- Rubric parsing ----------

PHASE_HEADER = re.compile(r"^##\s+Phase\s+(\d+)\s*—\s*([^\n]+?)\s*(?:✅|🟡|⬜)?\s*$")

# 5-column current orchestrator rubric: | id | criterion | status | evidence | gap |
ROW_5COL = re.compile(
    r"^\|\s*(\d+\.\d+)\s*\|\s*(.+?)\s*\|\s*(✅|🟡|⬜)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$"
)
# Legacy 4-column format: | id <status?> | criterion | test |  (kept for back-compat)
ROW_4COL = re.compile(
    r"^\|\s*(\d+\.\d+)\s*(✅|🟡|⬜)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$"
)

# Checklist-format rubric (e.g., MEMORY-ROUTER-READ-RUBRIC.md):
CHECKLIST = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+?)\s*$")
SECTION_HEADER = re.compile(r"^##\s+(.+?)\s*$")


def _status_from_emoji(em):
    if em == "✅":
        return "done"
    if em == "🟡":
        return "in_progress"
    return "todo"


def parse_phase(rubric_path: Path, phase: int) -> dict:
    """Return {phase, title, criteria: [{id, status, criterion, test, gap?}, ...]}.

    Handles both 5-col current orchestrator format AND 4-col legacy format.
    """
    text = rubric_path.read_text()
    lines = text.splitlines()
    out = {"phase": phase, "title": None, "criteria": []}
    in_phase = False
    for line in lines:
        m = PHASE_HEADER.match(line)
        if m:
            if int(m.group(1)) == phase:
                out["title"] = m.group(2).strip()
                in_phase = True
                continue
            elif in_phase:
                break  # next phase, stop
        if not in_phase:
            continue
        # Prefer 5-col; fall back to 4-col.
        m5 = ROW_5COL.match(line)
        if m5:
            cid, criterion, status_em, evidence, gap = m5.groups()
            if criterion.lower().startswith("criterion"):
                continue  # header row
            out["criteria"].append({
                "id": cid,
                "status": _status_from_emoji(status_em),
                "criterion": criterion.strip(),
                "test": evidence.strip(),  # evidence column doubles as test reference
                "gap": gap.strip(),
            })
            continue
        m4 = ROW_4COL.match(line)
        if m4:
            cid, status_em, criterion, test = m4.groups()
            if criterion.lower().startswith("criterion"):
                continue
            out["criteria"].append({
                "id": cid,
                "status": _status_from_emoji(status_em),
                "criterion": criterion.strip(),
                "test": test.strip(),
            })
    return out


def parse_checklist(rubric_path: Path) -> dict:
    """Parse a `- [ ]`/`- [x]` checklist rubric (e.g., MEMORY-ROUTER-READ-RUBRIC.md)
    into the same shape parse_phase returns. Sections become id-prefixes.
    """
    text = rubric_path.read_text()
    lines = text.splitlines()
    out = {"phase": 0, "title": rubric_path.stem, "criteria": []}
    section = "root"
    section_counters: dict[str, int] = {}
    for line in lines:
        m_hdr = SECTION_HEADER.match(line)
        if m_hdr:
            section = re.sub(r"[^a-z0-9]+", "-", m_hdr.group(1).lower()).strip("-")
            section_counters.setdefault(section, 0)
            continue
        m = CHECKLIST.match(line)
        if m:
            mark, criterion_text = m.groups()
            section_counters.setdefault(section, 0)
            section_counters[section] += 1
            done = mark.lower() == "x"
            out["criteria"].append({
                "id": f"{section}.{section_counters[section]}",
                "status": "done" if done else "todo",
                "criterion": criterion_text.strip(),
                "test": criterion_text.strip(),
            })
    return out


# ---------- Agent loop ----------

WORKER_SYSTEM = """You are the worker agent. Your job is to complete unmet criteria from a phase rubric for a project-management wiki build.

The criteria are testable. For each one, propose a concrete approach: code paths to change, commands to run, files to create. Do not invent tests — read the rubric's test column as the success criterion.

Respond with a structured plan, then execute step-by-step. When done, summarize what you changed and how you verified.
"""

GRADER_SYSTEM = """You are the grader agent. Given a rubric and the worker's report, score each criterion:
  - pass: worker's report demonstrates the test would pass
  - fail: missing or wrong; explain what's needed
  - skip: not applicable

Return JSON: {"criteria": {"id": {"verdict": "pass|fail|skip", "feedback": "..."}}, "overall_pass": bool}
"""


def call_claude(client, system: str, user: str,
                model: str = "claude-opus-4-7", max_tokens: int = 4000) -> str:
    """Single round-trip Claude call. Tries three backends in order:
      1. Anthropic API SDK (requires API key, not OAuth)
      2. Claude Code CLI in print mode (`claude -p`) — uses whatever subscription
         is logged in on this machine, no API key needed
      3. Error out cleanly

    The `client` arg is ignored when falling back to CLI; we keep it for
    backward compat with the original signature.
    """
    import os
    import subprocess

    # Backend 1: Anthropic SDK if API key is available
    if client is not None and getattr(client, "api_key", "").startswith("sk-ant-api"):
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")

    # Backend 2: claude -p CLI
    if subprocess.run(["which", "claude"], capture_output=True).returncode == 0:
        # Combine system + user since CLI handles them in --system-prompt
        try:
            result = subprocess.run(
                [
                    "claude", "-p",
                    "--output-format", "text",
                    "--system-prompt", system,
                    "--permission-mode", "default",
                ],
                input=user,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "[claude -p timed out at 180s]"
        if result.returncode != 0:
            return f"[claude -p exited {result.returncode}: {result.stderr[:500]}]"
        return result.stdout.strip()

    raise RuntimeError(
        "No Claude backend available: need either ANTHROPIC_API_KEY (with API key, not OAuth) "
        "OR `claude` CLI in PATH with a logged-in session."
    )


def _get_anthropic_client():
    """Try to construct an Anthropic SDK client. Returns None if unavailable."""
    try:
        import anthropic
    except ImportError:
        return None
    api_key = load_api_key()
    if not api_key or not api_key.startswith("sk-ant-api"):
        return None
    return anthropic.Anthropic(api_key=api_key)


def run_outcomes(rubric_path: Path, phase: int, max_iter: int = 5,
                 model: str = "claude-opus-4-7") -> dict:
    """Worker→grader loop. Stops when grader passes all unmet criteria or max_iter."""
    info = parse_phase(rubric_path, phase)
    if not info["title"]:
        raise SystemExit(f"Phase {phase} not found in rubric.")
    unmet = [c for c in info["criteria"] if c["status"] == "todo"]
    if not unmet:
        return {"phase": phase, "status": "already-done", "iterations": 0}

    client = _get_anthropic_client()    # may be None — call_claude falls back to `claude -p`
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history = []
    feedback = ""

    for it in range(1, max_iter + 1):
        worker_prompt = (
            f"# Phase {phase}: {info['title']}\n\n"
            f"## Unmet criteria\n\n"
            + "\n".join(f"- **{c['id']}**: {c['criterion']}  (test: {c['test']})" for c in unmet)
            + (f"\n\n## Prior grader feedback\n\n{feedback}\n" if feedback else "")
            + "\n\nDo the work. Be concrete: cite file paths, commands, expected outputs."
        )
        worker_out = call_claude(client, WORKER_SYSTEM, worker_prompt, model=model)

        grader_prompt = (
            f"# Rubric\n\n"
            + "\n".join(f"- {c['id']}: {c['criterion']} (test: {c['test']})" for c in unmet)
            + f"\n\n# Worker's report\n\n{worker_out}\n\n"
            "Grade as JSON only. No prose outside the JSON."
        )
        grader_out = call_claude(client, GRADER_SYSTEM, grader_prompt, model=model)
        # Best-effort JSON extract
        try:
            grade = json.loads(re.search(r"\{.*\}", grader_out, re.DOTALL).group())
        except (AttributeError, json.JSONDecodeError):
            grade = {"criteria": {}, "overall_pass": False, "raw": grader_out}

        history.append({"iteration": it, "worker": worker_out, "grade": grade})

        if grade.get("overall_pass"):
            break
        # Build feedback for next iteration
        feedback = "\n".join(
            f"- {cid}: {v.get('feedback', '')}"
            for cid, v in grade.get("criteria", {}).items()
            if v.get("verdict") == "fail"
        )

    report = {
        "run_id": run_id,
        "phase": phase,
        "title": info["title"],
        "iterations": len(history),
        "passed": history[-1]["grade"].get("overall_pass", False) if history else False,
        "history": history,
    }
    log_path = LOG_DIR / f"{run_id}-phase{phase}.json"
    log_path.write_text(json.dumps(report, indent=2))
    return report


def score_only(rubric_path: Path, phase: int | None = None,
                 checklist: bool = False) -> dict:
    """Parse the rubric and report counts without running any worker/grader.
    Useful for `outcomes_runner score --rubric ...` (B6).
    """
    if checklist:
        info = parse_checklist(rubric_path)
    else:
        if phase is None:
            raise SystemExit("--phase is required for table-format rubrics")
        info = parse_phase(rubric_path, phase)
    if not info.get("title") and not info["criteria"]:
        raise SystemExit(f"Nothing parsed from {rubric_path} (phase={phase}).")

    counts = {"done": 0, "in_progress": 0, "todo": 0}
    for c in info["criteria"]:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    total = sum(counts.values())
    pct = (100 * counts["done"] / total) if total else 0.0
    return {
        "rubric": str(rubric_path),
        "phase": info["phase"],
        "title": info["title"],
        "counts": counts,
        "total": total,
        "percent_done": round(pct, 1),
        "unmet": [c["id"] for c in info["criteria"] if c["status"] != "done"],
    }


def main() -> int:
    # Back-compat shim: detect legacy invocation (no subcommand, just flags).
    # If argv[1] is `score` or `run`, use subcommand parsing. Otherwise assume legacy `run`.
    argv = list(sys.argv[1:])
    if argv and argv[0] not in ("score", "run"):
        argv.insert(0, "run")  # Default subcommand for back-compat

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Worker→grader loop for unmet criteria")
    run_p.add_argument("--rubric", required=True, type=Path)
    run_p.add_argument("--phase", required=True, type=int)
    run_p.add_argument("--max-iter", type=int, default=5)
    run_p.add_argument("--model", default="claude-opus-4-7")

    score_p = sub.add_parser("score", help="Parse + count criteria, no LLM calls")
    score_p.add_argument("--rubric", required=True, type=Path)
    score_p.add_argument("--phase", type=int, default=None,
                           help="phase number (required for table-format rubrics)")
    score_p.add_argument("--checklist", action="store_true",
                           help="parse as checklist format (- [ ] / - [x])")

    args = ap.parse_args(argv)

    if args.cmd == "score":
        result = score_only(args.rubric, args.phase, args.checklist)
        print(json.dumps(result, indent=2))
        return 0

    # cmd == "run"
    report = run_outcomes(args.rubric, args.phase, args.max_iter, args.model)
    print(json.dumps({k: v for k, v in report.items() if k != "history"}, indent=2))
    print(f"\nFull log: {LOG_DIR}/{report['run_id']}-phase{args.phase}.json")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
