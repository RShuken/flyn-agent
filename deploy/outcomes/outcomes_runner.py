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
ROW = re.compile(r"^\|\s*(\d+\.\d+)\s*(✅|🟡|⬜)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")


def parse_phase(rubric_path: Path, phase: int) -> dict:
    """Return {phase, title, status_overall, criteria: [{id, status, criterion, test}, ...]}"""
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
        if in_phase:
            m = ROW.match(line)
            if m:
                cid, status_em, criterion, test = m.groups()
                # Skip header row
                if criterion.lower().startswith("criterion"):
                    continue
                status = ("done" if status_em == "✅"
                          else "in_progress" if status_em == "🟡"
                          else "todo")
                out["criteria"].append({
                    "id": cid,
                    "status": status,
                    "criterion": criterion.strip(),
                    "test": test.strip(),
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", required=True, type=Path)
    ap.add_argument("--phase", required=True, type=int)
    ap.add_argument("--max-iter", type=int, default=5)
    ap.add_argument("--model", default="claude-opus-4-7")
    args = ap.parse_args()

    report = run_outcomes(args.rubric, args.phase, args.max_iter, args.model)
    print(json.dumps({k: v for k, v in report.items() if k != "history"}, indent=2))
    print(f"\nFull log: {LOG_DIR}/{report['run_id']}-phase{args.phase}.json")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
