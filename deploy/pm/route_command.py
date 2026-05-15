#!/usr/bin/env python3
"""Handler for the Telegram `/route <index> <project|skip>` command.

Parses the command, looks up the meeting_id in the morning-digest's
state file, and routes (or marks dropped). Returns a dict the gateway
can use to reply.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wiki-backend"))

from _lib import load_project, route_meeting_to_project  # noqa: E402
import meetings_db  # noqa: E402


DEFAULT_STATE = Path.home() / ".openclaw" / "state" / "last-review-list.json"


def _load_state(state_path: Path) -> list[dict]:
    if not state_path.exists():
        return []
    try:
        return json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return []


def _meeting_id_for_index(state: list[dict], idx: int) -> str | None:
    for row in state:
        if row.get("index") == idx:
            return row.get("meeting_id")
    return None


def handle(message: str, state_path: Path | None = None) -> dict:
    """Parse and execute. Returns {ok: bool, reply: str}."""
    state_path = state_path or DEFAULT_STATE
    parts = message.strip().split()
    if len(parts) < 3 or parts[0] != "/route":
        return {"ok": False,
                "reply": "Usage: /route <index> <project-slug | skip>"}
    try:
        idx = int(parts[1])
    except ValueError:
        return {"ok": False, "reply": f"Index must be a number, got '{parts[1]}'"}
    target = parts[2].lower()

    state = _load_state(state_path)
    meeting_id = _meeting_id_for_index(state, idx)
    if not meeting_id:
        return {"ok": False,
                "reply": f"No meeting at index {idx} in today's review list."}

    meetings_db.init_db()
    conn = meetings_db._connect()
    try:
        row = conn.execute(
            "SELECT * FROM meetings WHERE meeting_id=?", (meeting_id,)
        ).fetchone()
        if not row:
            return {"ok": False,
                    "reply": f"Meeting {meeting_id} no longer in DB."}
        if row["status"] in ("routed", "dropped"):
            return {"ok": False,
                    "reply": f"Meeting {meeting_id} is already "
                             f"{row['status']}; no-op."}

        if target == "skip":
            conn.execute(
                "UPDATE meetings SET status='dropped', "
                "classifier_reason='manual skip', "
                "updated_at=datetime('now') WHERE meeting_id=?",
                (meeting_id,),
            )
            meetings_db.audit(
                conn, actor="route-cmd", meeting_id=meeting_id,
                action="dropped",
                payload={"index": idx},
            )
            return {"ok": True, "reply": f"Meeting {idx} ({row['title']}) dropped."}

        try:
            cfg = load_project(target)
        except FileNotFoundError:
            return {"ok": False, "reply": f"Unknown project '{target}'."}

        meeting = dict(row)
        try:
            meeting["attendees"] = json.loads(meeting.get("attendees") or "[]")
        except json.JSONDecodeError:
            meeting["attendees"] = []

        try:
            res = route_meeting_to_project(meeting, cfg)
        except Exception as e:  # noqa: BLE001
            return {"ok": False,
                    "reply": f"Routing failed: {type(e).__name__}: {e}"}

        conn.execute(
            "UPDATE meetings SET status='routed', routed_project=?, "
            "routed_commit_sha=?, routed_at=datetime('now'), "
            "updated_at=datetime('now') WHERE meeting_id=?",
            (cfg.slug, res["commit_sha"], meeting_id),
        )
        meetings_db.audit(
            conn, actor="route-cmd", meeting_id=meeting_id,
            action="routed",
            payload={"project": cfg.slug, "sha": res["commit_sha"]},
        )
        return {"ok": True,
                "reply": f"Routed to {cfg.slug} @ {res['commit_sha'][:8]}"}
    finally:
        conn.close()


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("message", help="The /route command text")
    args = ap.parse_args()
    out = handle(args.message)
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
