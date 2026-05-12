#!/usr/bin/env python3
"""Seed the OL wiki backend SQLite DB from the markdown registry.

Idempotent: UPSERTs each question. Preserves status/answered_* fields when
the row already exists (so we don't blow away mutations made via API).

Usage:
  python seed.py [--project openliteracy] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path.home() / ".openclaw" / "scripts" / "flyn" / "pm"))

from db import DB_PATH, init_db  # type: ignore[import]

# import the existing registry parser
try:
    import registry_parser as rp  # type: ignore[import]
except ImportError:
    print("FATAL: can't find registry_parser. Run installer first.", file=sys.stderr)
    sys.exit(2)


def _strip_md_bold(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", s)


def _sprint_for_question(sprint_plan_md: Path, qid: str) -> int | None:
    """Cheap parse of 01_sprint-plan_Beth.md — find which Sprint section qid lives in."""
    if not sprint_plan_md.exists():
        return None
    current: int | None = None
    for line in sprint_plan_md.read_text().splitlines():
        m = re.match(r"^##\s+Sprint\s+(\d+)", line)
        if m:
            current = int(m.group(1))
            continue
        if current is not None and re.match(rf"^\|\s*{re.escape(qid)}\s*\|", line):
            return current
    return None


def upsert_question(conn: sqlite3.Connection, q: dict, sprint_plan_md: Path) -> str:
    """Returns 'inserted' or 'updated' or 'noop'."""
    target_sprint = _sprint_for_question(sprint_plan_md, q["id"])
    existing = conn.execute("SELECT id, status FROM questions WHERE id = ?", (q["id"],)).fetchone()
    args = {
        "id": q["id"],
        "section": q["section"],
        "section_title": q["section_title"],
        "text": _strip_md_bold(q["text"]),
        "ask": _strip_md_bold(q.get("ask")),
        "bucket": q["bucket"],
        "source": q.get("source"),
        "owner": q["owner"],
        "depends_on": json.dumps(q.get("depends_on") or []),
        "target_sprint": target_sprint,
    }
    if existing:
        # Preserve status / answered_* — only refresh content + ownership
        conn.execute(
            """
            UPDATE questions SET
                section = :section,
                section_title = :section_title,
                text = :text,
                ask = :ask,
                bucket = :bucket,
                source = :source,
                owner = :owner,
                depends_on = :depends_on,
                target_sprint = :target_sprint,
                updated_at = datetime('now')
            WHERE id = :id
            """,
            args,
        )
        return "updated"
    else:
        args.update({"status": "open"})
        conn.execute(
            """
            INSERT INTO questions
              (id, section, section_title, text, ask, bucket, source, owner,
               status, depends_on, target_sprint)
            VALUES
              (:id, :section, :section_title, :text, :ask, :bucket, :source, :owner,
               :status, :depends_on, :target_sprint)
            """,
            args,
        )
        return "inserted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="openliteracy")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = rp.load_project(args.project)
    registry_md = cfg.registry_path
    sprint_plan_md = registry_md.parent / "01_sprint-plan_Beth.md"

    if args.dry_run:
        qs = rp.parse_registry(registry_md)
        print(f"would seed {len(qs)} questions from {registry_md}")
        print(f"sprint plan: {sprint_plan_md} ({'present' if sprint_plan_md.exists() else 'MISSING'})")
        return 0

    init_db()
    qs = rp.parse_registry(registry_md)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    counts = {"inserted": 0, "updated": 0}
    for q in qs:
        action = upsert_question(conn, q, sprint_plan_md)
        counts[action] = counts.get(action, 0) + 1
    # Annotate source_doc on a fresh column update
    conn.execute(
        "UPDATE questions SET source_doc = ? WHERE source_doc IS NULL",
        (str(registry_md.relative_to(cfg.repo_path)),),
    )
    print(f"seed done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
