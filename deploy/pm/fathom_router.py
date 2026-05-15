#!/usr/bin/env python3
"""Poll Fathom for new meeting transcripts and route project-relevant ones
into the project repo + Graphiti.

Status: SKELETON. The polling + filtering logic is here; Fathom auth + transcript
fetch is delegated to a TODO until we wire in a service-account API key (the
mcp-remote OAuth flow we used for Ryan's Claude Code is per-user; it can't run
unattended in a cron).

Modes:
  (default)    Poll Fathom since last run; route any project-relevant meetings.
  --manual     Skip the poll, route a transcript that was hand-placed at
               --transcript <path>.
  --since      Override the "last run" timestamp (ISO8601).

When a project-relevant transcript is found:
  1. Pull repo, git pull --rebase.
  2. Drop the transcript at meeting_folder_template (cfg.fathom.meeting_folder_template).
  3. Append a WORKLOG.md entry (per the repo's CLAUDE.md session-lifecycle rule).
  4. Commit + push.
  5. POST to Graphiti as an episode tagged with attendees, project slug, decisions.
  6. Telegram-ping operators.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _lib import (  # noqa: E402
    ProjectConfig,
    git_commit_and_push,
    git_pull,
    graphiti_episode,
    load_project,
    telegram_send,
)


STATE_FILE = Path.home() / ".openclaw" / "projects" / "_fathom-router-state.json"


def is_project_relevant(meeting: dict, cfg: ProjectConfig) -> bool:
    """Match meeting against this project's fathom filters.

    A meeting is project-relevant if:
      - at least one attendee email matches filter_attendees, OR
      - the title contains any filter_title_substrings.
    """
    fathom = cfg.raw.get("fathom", {})
    attendees = {a.lower() for a in meeting.get("attendees", [])}
    title = meeting.get("title", "").lower()
    if attendees & {a.lower() for a in fathom.get("filter_attendees", [])}:
        return True
    if any(sub.lower() in title for sub in fathom.get("filter_title_substrings", [])):
        return True
    return False


def fetch_recent_meetings(since: datetime) -> list[dict]:
    """TODO: Call Fathom API to list meetings recorded since `since`.

    Until a service-account key is wired in, this returns []. The Operator can
    run --manual to route a transcript that mcp-remote already pulled into the
    user-level Claude Code session and dropped on disk.
    """
    # When implementing:
    #   GET https://api.fathom.ai/external/v1/meetings?created_after=<iso>
    #   Authorization: Bearer <FATHOM_API_KEY>
    return []


def route_transcript(cfg: ProjectConfig, meeting: dict, transcript_path: Path) -> str:
    """Place transcript in repo, commit, ingest. Returns commit SHA."""
    git_pull(cfg.repo_path)

    # Build target path from template
    template = cfg.raw["fathom"]["meeting_folder_template"]
    target_rel = template.format(
        date=meeting["date"],          # YYYY-MM-DD
        slug=meeting["short_slug"],    # e.g. "sprint2-mid-check"
    )
    target = cfg.repo_path / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)

    # Add header + body
    header = _header_block(cfg, meeting)
    target.write_text(header + "\n\n## Transcript\n\n" + transcript_path.read_text())

    # WORKLOG entry (prepend after the existing header section)
    _append_worklog(cfg.repo_path, meeting, target_rel)

    sha = git_commit_and_push(
        cfg.repo_path,
        paths=[str(target_rel), "WORKLOG.md"],
        message=(
            f"docs(meetings): add Fathom transcript for {meeting['date']} "
            f"{meeting['short_slug']} (auto-routed by Flyn)"
        ),
    )
    return sha


def ingest_to_graphiti(cfg: ProjectConfig, meeting: dict, sha: str) -> None:
    body = (
        f"On {meeting['date']}, project {cfg.display_name} (slug {cfg.slug}) "
        f"had meeting titled '{meeting['title']}' attended by "
        f"{', '.join(meeting['attendees'])}. "
        f"Transcript filed at commit {sha[:8]} in the planning repo."
    )
    episode_name = f"{cfg.slug}-meeting-{meeting['date']}-{meeting['short_slug']}"

    # New: route through the MemoryRouter (port 8400).
    # Wrapped in try/except so a router outage does not crash the Fathom flow.
    try:
        _router_payload = json.dumps({
            "source": "fathom",
            "event_type": "meeting_summary",
            "subject": f"meeting-{meeting.get('short_slug', episode_name)}",
            "body": body,
            "dedup_key": f"fathom-{cfg.slug}-{meeting['date']}-{meeting.get('short_slug', '')}",
            "raw_payload": {
                "date": meeting["date"],
                "short_slug": meeting.get("short_slug"),
                "project": cfg.slug,
            },
        }).encode()
        _router_req = urllib.request.Request(
            "http://127.0.0.1:8400/api/memory/ingest",
            data=_router_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(_router_req, timeout=10)
    except Exception:  # noqa: BLE001
        # Router is down or not yet deployed — fall through to legacy path.
        pass

    # Legacy: direct Graphiti POST. Gated by FLYN_MEMORY_ROUTER_PASSTHROUGH
    # (default "true") so the pipeline keeps writing to Graphiti during
    # migration. Set to "false" once the router is the sole source of truth.
    if os.environ.get("FLYN_MEMORY_ROUTER_PASSTHROUGH", "true").lower() == "true":
        graphiti_episode(body=body, name=episode_name)


def notify_operators(cfg: ProjectConfig, meeting: dict, sha: str) -> None:
    by_name = {s.name.lower(): s for s in cfg.stakeholders}
    for recipient in cfg.raw["cadence"]["morning_standup"].get("recipients", []):
        s = by_name.get(recipient.lower())
        if s and s.chat_id and s.chat_id != "TBD":
            telegram_send(
                s.chat_id,
                f"📄 New transcript filed: *{meeting['title']}* "
                f"({meeting['date']}). Commit `{sha[:8]}`. "
                f"I'll surface new questions in tomorrow's standup.",
            )


def _header_block(cfg: ProjectConfig, meeting: dict) -> str:
    attendees = ", ".join(meeting["attendees"])
    return (
        f"# {meeting['title']}\n\n"
        f"- **Date:** {meeting['date']}\n"
        f"- **Source:** Fathom (auto-ingested by Flyn)\n"
        f"- **Recording:** {meeting['recording_url']}\n"
        f"- **Recorded by:** {meeting.get('recorded_by', 'Ryan Shuken')}\n"
        f"- **Attendees:** {attendees}\n"
    )


def _append_worklog(repo: Path, meeting: dict, target_rel: str) -> None:
    """Prepend a session entry to WORKLOG.md after the header block.

    Mimics the format from the repo's existing entries.
    """
    worklog = repo / "WORKLOG.md"
    body = worklog.read_text()
    entry = (
        f"\n## {datetime.now().strftime('%Y-%m-%d')} — Flyn (auto-ingest)\n"
        f"**Did:** Auto-routed Fathom transcript for {meeting['title']} "
        f"({meeting['date']}) into `{target_rel}`. Re-ingested to Graphiti.\n"
        f"**Decided:** Nothing — auto-pipeline; review at next standup.\n"
        f"**Open questions raised:** TBD — registry-parser diff pending.\n"
        f"**Current focus:** Same as prior. Flyn will surface new questions tomorrow 8am.\n\n"
        f"---"
    )
    # Insert after the first `---` separator (end of header block)
    pieces = body.split("\n---\n", 1)
    if len(pieces) == 2:
        worklog.write_text(pieces[0] + "\n---\n" + entry + "\n" + pieces[1])
    else:
        worklog.write_text(entry + "\n\n" + body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--manual", action="store_true",
                    help="Don't poll Fathom; route a hand-placed transcript")
    ap.add_argument("--transcript", help="Path to transcript when --manual")
    ap.add_argument("--since", help="ISO timestamp override")
    args = ap.parse_args()

    cfg = load_project(args.project)

    if args.manual:
        if not args.transcript:
            ap.error("--manual requires --transcript")
        # In manual mode, caller must also supply a meeting metadata file
        # next to the transcript (transcript.json) with title/date/attendees.
        import json
        meta_path = Path(args.transcript).with_suffix(".json")
        meeting = json.loads(meta_path.read_text())
        sha = route_transcript(cfg, meeting, Path(args.transcript))
        ingest_to_graphiti(cfg, meeting, sha)
        notify_operators(cfg, meeting, sha)
        return 0

    since = (
        datetime.fromisoformat(args.since)
        if args.since
        else datetime.now(timezone.utc) - timedelta(hours=2)
    )
    for meeting in fetch_recent_meetings(since):
        if not is_project_relevant(meeting, cfg):
            continue
        # TODO: download transcript to a tmp path, then call route_transcript.
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
