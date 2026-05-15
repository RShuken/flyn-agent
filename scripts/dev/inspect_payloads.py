#!/usr/bin/env python3
"""Pretty-print the last N raw payloads from meeting_events.

Useful during the first week: real Krisp payloads land here and we
adjust krisp_webhook._extract_meeting_fields() against what we actually
see.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    args = ap.parse_args()
    db = Path(os.environ.get(
        "FLYN_MEETINGS_DB",
        str(Path.home() / ".openclaw" / "data" / "flyn-meetings.db"),
    ))
    if not db.exists():
        print(f"no db at {db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_id, received_at, event_type, meeting_id, raw_payload "
        "FROM meeting_events ORDER BY id DESC LIMIT ?",
        (args.n,),
    ).fetchall()
    conn.close()
    for r in rows:
        print(f"=== {r['received_at']}  event_id={r['event_id']}  "
              f"type={r['event_type']}  meeting_id={r['meeting_id']} ===")
        try:
            print(json.dumps(json.loads(r["raw_payload"]), indent=2))
        except json.JSONDecodeError:
            print(r["raw_payload"])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
