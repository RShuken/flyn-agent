#!/usr/bin/env python3
"""Draft client communications and stage them for operator approval.

Status: SKELETON. The hard part — drafting in Beth's voice while respecting
per-stakeholder communication patterns — is an LLM call that we want to keep
swappable (env var PM_DRAFT_MODEL). This script's job is the workflow scaffolding:
read context, prompt the model, write the draft to the approval queue, ping
operator on Telegram, then send only after explicit approval comes back.

Modes:
  draft <question_ids...>    Compose a chase/follow-up email targeting these questions.
  status <free-text>         Compose a status reply to a client message.
  approve <draft_id>         Mark a queued draft as approved → send it.
  list                       Show queued drafts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _lib import ProjectConfig, load_project, telegram_send  # noqa: E402
from registry_parser import parse_registry  # type: ignore[import-not-found]  # noqa: E402


QUEUE_DIR = Path.home() / ".openclaw" / "projects" / "_drafts-queue"


def _ensure_queue() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def _model() -> str:
    return os.environ.get("PM_DRAFT_MODEL", "openai-codex/gpt-5.4")


def draft_chase_email(cfg: ProjectConfig, qids: list[str]) -> dict:
    """Produce a draft email asking the most relevant client stakeholder for
    answers to the given question IDs.

    Returns a queue record. Does NOT send.
    """
    all_qs = {q["id"]: q for q in parse_registry(cfg.registry_path)}
    questions = [all_qs[qid] for qid in qids if qid in all_qs]
    if not questions:
        raise ValueError(f"No matching questions for {qids}")

    # Group by owner to figure out who to address
    owners = {q["owner"] for q in questions}
    if len(owners) > 1:
        primary_owner = max(owners, key=lambda o: sum(1 for q in questions if q["owner"] == o))
    else:
        primary_owner = next(iter(owners))

    operator = next((s for s in cfg.stakeholders if s.approval_gate), None)
    operator_voice = operator.name if operator else "Beth Kukla"

    prompt = _build_chase_prompt(cfg, primary_owner, questions, operator_voice)
    draft_text = _call_model(prompt)

    record = {
        "id": str(uuid.uuid4())[:8],
        "project": cfg.slug,
        "type": "chase",
        "recipient": primary_owner,
        "question_ids": qids,
        "voice": operator_voice,
        "model": _model(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "awaiting-approval",
        "draft": draft_text,
    }
    _enqueue(record)
    _notify_approver(cfg, record)
    return record


def _build_chase_prompt(
    cfg: ProjectConfig, recipient: str, questions: list[dict], voice: str
) -> str:
    qlines = "\n".join(
        f"- {q['id']}: {q['ask'] or q['text']}" for q in questions
    )
    tone_path = Path.home() / ".openclaw" / "projects" / cfg.slug / "comms-tone.md"
    tone = tone_path.read_text() if tone_path.exists() else (
        "Friendly-professional, brief, organized. No corporate-speak. "
        "Acknowledges the recipient is busy. Asks for a written reply, not a meeting. "
        "Numbered list when more than one question."
    )
    return (
        f"You are drafting an email from {voice} (PM) to {recipient}, the program lead "
        f"for project {cfg.display_name}.\n\n"
        f"GOAL: Get written answers to the listed open questions. Recipient is "
        f"time-constrained (see project config). Avoid asking for a meeting.\n\n"
        f"TONE:\n{tone}\n\n"
        f"QUESTIONS TO ASK:\n{qlines}\n\n"
        f"Produce only the email body (no Subject line yet). Use markdown."
    )


def _call_model(prompt: str) -> str:
    """Invoke the drafting LLM via OpenClaw's agent shell.

    For v0 we shell out to `openclaw agent --agent main -m "<prompt>"` and capture
    the reply. A future revision will route through a dedicated drafting profile
    so this doesn't burn the main agent's turn.
    """
    try:
        result = subprocess.run(
            ["openclaw", "agent", "--agent", "main", "-m", prompt],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        # Dev-mode dry: return a placeholder so the queue + approval flow
        # can be exercised without OpenClaw installed locally.
        return f"[DRAFT placeholder — model={_model()} would have rendered this prompt]"


def _enqueue(record: dict) -> None:
    _ensure_queue()
    path = QUEUE_DIR / f"{record['project']}-{record['id']}.json"
    path.write_text(json.dumps(record, indent=2))


def _notify_approver(cfg: ProjectConfig, record: dict) -> None:
    approver = cfg.approvers()[0] if cfg.approvers() else None
    if not approver or not approver.chat_id or approver.chat_id == "TBD":
        return
    topic = cfg.raw.get("comms_autonomy", {}).get("approval_topic")
    body = (
        f"📝 *Draft ready for your approval* — `{record['id']}`\n"
        f"To: {record['recipient']}\n"
        f"Asks about: {', '.join(record['question_ids'])}\n\n"
        f"---\n{record['draft']}\n---\n\n"
        f"Reply with:\n"
        f"• `approve {record['id']}` — I'll send it\n"
        f"• `edit {record['id']}` — paste a revised version\n"
        f"• `skip {record['id']}` — drop the draft"
    )
    telegram_send(approver.chat_id, body, topic_id=topic)


def approve(draft_id: str) -> None:
    """Mark a queued draft as approved. Actual send-via-email is a TODO that
    will plug into the operator's preferred email integration (Gmail MCP for
    Ryan, Apple Mail for Beth — TBD). For now, this just flips status."""
    path = next(QUEUE_DIR.glob(f"*-{draft_id}.json"), None)
    if not path:
        raise FileNotFoundError(f"Draft {draft_id} not found in queue")
    record = json.loads(path.read_text())
    record["status"] = "approved"
    record["approved_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(record, indent=2))
    # TODO: actually send via email here.


def list_queue() -> None:
    if not QUEUE_DIR.exists():
        print("Queue is empty.")
        return
    for path in sorted(QUEUE_DIR.glob("*.json")):
        r = json.loads(path.read_text())
        print(
            f"{r['id']:>8}  {r['status']:<18}  {r['project']:<14}  "
            f"→ {r['recipient']}  ({r['type']})"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_draft = sub.add_parser("draft")
    p_draft.add_argument("--project", required=True)
    p_draft.add_argument("question_ids", nargs="+")
    p_approve = sub.add_parser("approve")
    p_approve.add_argument("draft_id")
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "draft":
        cfg = load_project(args.project)
        record = draft_chase_email(cfg, args.question_ids)
        print(f"Drafted {record['id']} — see Telegram approval topic.")
    elif args.cmd == "approve":
        approve(args.draft_id)
    elif args.cmd == "list":
        list_queue()
    return 0


if __name__ == "__main__":
    sys.exit(main())
