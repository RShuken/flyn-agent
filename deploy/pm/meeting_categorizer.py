#!/usr/bin/env python3
"""Nightly meeting categorizer.

For each meeting with status='pending':
  1. Try rules (attendee/title).
  2. Fall back to claude -p.
  3. If a project is matched with sufficient confidence, route it
     (write into repo, push, ingest, ping). Mark 'routed'.
  4. Otherwise mark 'review' for the morning digest to surface.

Also un-sticks rows left in 'classifying' >1h from a previous crash.

Usage:
  python3 meeting_categorizer.py            # one pass
  python3 meeting_categorizer.py --noop     # classify but don't route or write
  python3 meeting_categorizer.py --unstick  # only revert stuck rows
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wiki-backend"))

from _lib import (  # noqa: E402
    ProjectConfig,
    PROJECTS_ROOT,
    load_project,
    route_meeting_to_project,
)
from meeting_classifier import classify_by_rules, classify_by_llm  # noqa: E402
import meetings_db  # noqa: E402


def list_projects_for_classifier() -> list[ProjectConfig]:
    """Read every project config under ~/.openclaw/projects/."""
    out = []
    if not PROJECTS_ROOT.exists():
        return out
    for sub in sorted(PROJECTS_ROOT.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "config.yaml").exists():
            continue
        try:
            out.append(load_project(sub.name))
        except Exception as e:  # noqa: BLE001
            print(f"[categorizer] skipping {sub.name}: {e}", file=sys.stderr)
    return out


def unstick_old_classifying() -> int:
    """Revert rows stuck in 'classifying' >1h."""
    meetings_db.init_db()
    conn = meetings_db._connect()
    try:
        cur = conn.execute(
            "UPDATE meetings SET status='pending', "
            "updated_at=datetime('now') "
            "WHERE status='classifying' AND "
            "(julianday('now') - julianday(updated_at)) * 24 >= 1"
        )
        return cur.rowcount or 0
    finally:
        conn.close()


def _meeting_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["attendees"] = json.loads(d.get("attendees") or "[]")
    except json.JSONDecodeError:
        d["attendees"] = []
    return d


def run_once(noop: bool = False) -> dict[str, int]:
    """One categorizer pass. Returns counts keyed by outcome."""
    meetings_db.init_db()
    counts = {"routed": 0, "review": 0, "error": 0, "skipped": 0}
    projects = list_projects_for_classifier()
    conn = meetings_db._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM meetings WHERE status='pending'"
        ).fetchall()
        for row in rows:
            meeting = _meeting_row_to_dict(row)
            mid = meeting["meeting_id"]
            conn.execute(
                "UPDATE meetings SET status='classifying', "
                "updated_at=datetime('now') WHERE meeting_id=?",
                (mid,),
            )

            slug, conf, reason = classify_by_rules(meeting, projects)
            if not slug:
                slug, conf, reason = classify_by_llm(meeting, projects)

            if slug and conf in ("rule", "llm-high"):
                if noop:
                    new_status, counts_key = "pending", "skipped"
                    conn.execute(
                        "UPDATE meetings SET status=?, classifier_reason=?, "
                        "classifier_confidence=?, updated_at=datetime('now') "
                        "WHERE meeting_id=?",
                        (new_status, reason, conf, mid),
                    )
                    counts[counts_key] += 1
                    continue
                try:
                    cfg = next(p for p in projects if p.slug == slug)
                    res = route_meeting_to_project(meeting, cfg)
                    conn.execute(
                        "UPDATE meetings SET status='routed', "
                        "routed_project=?, routed_commit_sha=?, "
                        "classifier_reason=?, classifier_confidence=?, "
                        "routed_at=datetime('now'), updated_at=datetime('now') "
                        "WHERE meeting_id=?",
                        (slug, res["commit_sha"], reason, conf, mid),
                    )
                    meetings_db.audit(
                        conn, actor="categorizer", meeting_id=mid,
                        action="routed",
                        payload={"project": slug, "sha": res["commit_sha"]},
                    )
                    counts["routed"] += 1
                except Exception as e:  # noqa: BLE001
                    conn.execute(
                        "UPDATE meetings SET status='error', "
                        "classifier_reason=?, updated_at=datetime('now') "
                        "WHERE meeting_id=?",
                        (f"route failed: {e}", mid),
                    )
                    meetings_db.audit(
                        conn, actor="categorizer", meeting_id=mid,
                        action="route_failed",
                        payload={"error": str(e)},
                    )
                    counts["error"] += 1
            else:
                conn.execute(
                    "UPDATE meetings SET status='review', "
                    "classifier_reason=?, classifier_confidence=?, "
                    "updated_at=datetime('now') WHERE meeting_id=?",
                    (reason, conf, mid),
                )
                meetings_db.audit(
                    conn, actor="categorizer", meeting_id=mid,
                    action="marked_review",
                    payload={"reason": reason, "confidence": conf},
                )
                counts["review"] += 1
    finally:
        conn.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--noop", action="store_true",
                    help="Classify but don't route or change state")
    ap.add_argument("--unstick", action="store_true",
                    help="Only revert stuck 'classifying' rows, then exit")
    args = ap.parse_args()

    if args.unstick:
        n = unstick_old_classifying()
        print(f"unstuck {n} row(s)")
        return 0

    unstick_old_classifying()
    counts = run_once(noop=args.noop)
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
