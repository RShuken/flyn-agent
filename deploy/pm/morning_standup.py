#!/usr/bin/env python3
"""Compose Flyn's morning standup digest for a project and send to Telegram.

Modes:
  (default)         Compose + send to operator chat
  --dry-run         Compose, print to stdout, do not send
  --deadline-only   Skip the new-overnight + critical-path sections, just show
                    stakeholder lockout warnings (used by the 6am deadline-watch cron)

The digest stays under ~250 words per workspace/PROJECTS.md Rule 7.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).parent))
from _lib import (  # noqa: E402
    ProjectConfig,
    graphiti_health,
    graphiti_search,
    load_project,
    telegram_send,
)
from registry_parser import parse_registry  # type: ignore[import-not-found]  # noqa: E402


def compose_digest(cfg: ProjectConfig, deadline_only: bool = False) -> str:
    today = date.today()
    sprint_ends_raw = cfg.current_sprint_ends
    sprint_ends = date.fromisoformat(sprint_ends_raw) if sprint_ends_raw else None
    days_to_sprint_end = (sprint_ends - today).days if sprint_ends else None

    lines: list[str] = []
    lines.append(f"☀️ *Flyn standup — {cfg.display_name}*")
    lines.append(f"_Sprint {cfg.current_sprint}, day { _day_in_sprint(cfg, today) }._")
    if days_to_sprint_end is not None:
        lines.append(f"Sprint ends in *{days_to_sprint_end} days* ({sprint_ends}).")
    lines.append("")

    # Stakeholder lockout warnings — runs in both modes
    warnings = stakeholder_warnings(cfg, today)
    if warnings:
        lines.append("⏳ *Deadline watch*")
        lines.extend(f"• {w}" for w in warnings)
        lines.append("")

    if deadline_only:
        return "\n".join(lines).rstrip()

    # Critical path: open questions blocking current sprint exit
    blockers = sprint_blockers(cfg)
    if blockers:
        lines.append(f"🚧 *Blocking sprint exit ({len(blockers)} open)*")
        for q in blockers[:5]:
            owner = q.get("owner", "?")
            lines.append(f"• `{q['id']}` ({owner}): {_short(q['text'], 90)}")
        if len(blockers) > 5:
            lines.append(f"…and {len(blockers) - 5} more.")
        lines.append("")

    # Overnight: new transcripts + new questions
    overnight = overnight_summary(cfg)
    if overnight:
        lines.append("🌙 *Overnight*")
        lines.extend(f"• {item}" for item in overnight)
        lines.append("")

    # Drafts awaiting approval
    pending_drafts = drafts_awaiting_approval(cfg)
    if pending_drafts:
        lines.append(f"📝 *Drafts awaiting approval ({pending_drafts})*")
        topic = cfg.raw.get("comms_autonomy", {}).get("approval_topic")
        if topic:
            lines.append(f"See topic {topic}.")
        lines.append("")

    # Flyn's asks
    asks = flyn_asks(cfg)
    if asks:
        lines.append("❓ *My asks*")
        lines.extend(f"• {a}" for a in asks)
        lines.append("")

    if not graphiti_health():
        lines.append("_⚠️ Graphiti is down — running off repo markdown only._")

    return "\n".join(lines).rstrip()


def _day_in_sprint(cfg: ProjectConfig, today: date) -> int:
    start_str = cfg.raw.get("contract", {}).get("start")
    if not start_str:
        return 0
    start = date.fromisoformat(start_str)
    sprint_len = cfg.raw["contract"]["sprint_duration_days"]
    return ((today - start).days % sprint_len) + 1


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def stakeholder_warnings(cfg: ProjectConfig, today: date) -> list[str]:
    """Return human-readable warnings for any stakeholder whose unavailability
    window opens within `warn_days_before` days."""
    warn_days = cfg.raw["cadence"]["deadline_watch"]["warn_days_before"]
    warnings: list[str] = []
    for s in cfg.stakeholders:
        if not s.timeline_constraint:
            continue
        from_str = s.timeline_constraint.get("unavailable_from")
        if not from_str:
            continue
        unavail = date.fromisoformat(from_str)
        days_until = (unavail - today).days
        if 0 < days_until <= warn_days:
            open_for_s = [
                q for q in _all_open(cfg) if q.get("owner") == s.name
            ]
            warnings.append(
                f"*{s.name}* off {unavail} (in {days_until}d). "
                f"{len(open_for_s)} open question(s) owned by them."
            )
        elif days_until <= 0:
            warnings.append(
                f"*{s.name}* unreachable until "
                f"{s.timeline_constraint.get('unavailable_to')}. "
                f"Re-route any open questions."
            )
    return warnings


def sprint_blockers(cfg: ProjectConfig) -> list[dict]:
    """Questions tagged for current sprint (or unscheduled) and still open.

    For OL, Beth's sprint plan tags each question with a target sprint in a
    parallel doc (`01_sprint-plan_Beth.md`). For v0, we treat all open
    questions as 'potentially blocking' and let the operator filter.
    """
    return _all_open(cfg)


def overnight_summary(cfg: ProjectConfig) -> list[str]:
    """Look for episodes ingested in the last 18h tagged with this project.

    Best-effort: if Graphiti is unreachable or returns no results, return [].
    Never raise — the standup should still post even when KG is down.
    """
    try:
        facts = graphiti_search(f"{cfg.slug} ingested")
    except Exception:
        return []
    if not facts:
        return []
    cutoff = (datetime.utcnow() - timedelta(hours=18)).isoformat()
    fresh = [f for f in facts if f.get("created_at", "") > cutoff]
    return [f"{f.get('name', '?')}" for f in fresh[:5]]


def drafts_awaiting_approval(cfg: ProjectConfig) -> int:
    try:
        facts = graphiti_search(f"{cfg.slug} draft awaiting-approval")
    except Exception:
        return 0
    return len(facts)


def flyn_asks(cfg: ProjectConfig) -> list[str]:
    """Anything Flyn needs from the operator before it can proceed."""
    asks: list[str] = []
    if any(s.name == "TBD" or s.chat_id == "TBD" for s in cfg.stakeholders):
        asks.append("Fill in stakeholder chat_id/email TBDs in config.yaml.")
    # Two parallel registries unreconciled?
    parallel = cfg.raw.get("source_of_truth", {}).get("parallel_views", [])
    if parallel:
        asks.append(
            f"Two parallel registries tracked ({len(parallel) + 1} total). "
            "Designate one canonical or confirm current pick."
        )
    return asks


def _all_open(cfg: ProjectConfig) -> list[dict]:
    """Pull all open questions from the canonical registry markdown.

    Falls back to repo-only when Graphiti is down (per workspace/PROJECTS.md).
    """
    return parse_registry(cfg.registry_path)


def deliver(cfg: ProjectConfig, digest: str, dry_run: bool) -> None:
    if dry_run:
        print(digest)
        return
    cadence = cfg.raw["cadence"]["morning_standup"]
    if not cadence.get("enabled", True):
        return

    # Recipient lookup: config uses nicknames ("ryan", "beth"); stakeholder
    # name is full ("Ryan Shuken"). Match by lowercased first name OR by
    # full lowercased name.
    by_alias = {}
    for s in cfg.stakeholders:
        by_alias[s.name.lower()] = s
        by_alias[s.name.split()[0].lower()] = s

    for recipient_key in cadence.get("recipients", []):
        s = by_alias.get(recipient_key.lower())
        if s and s.chat_id and s.chat_id != "TBD":
            try:
                telegram_send(s.chat_id, digest)
            except Exception as exc:
                # Don't let one failed recipient block delivery to others
                print(f"deliver: failed to {s.name} ({s.chat_id}): {exc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deadline-only", action="store_true")
    args = ap.parse_args()
    cfg = load_project(args.project)
    digest = compose_digest(cfg, deadline_only=args.deadline_only)
    deliver(cfg, digest, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
