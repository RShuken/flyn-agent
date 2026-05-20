#!/usr/bin/env bash
# Daily backfill for conversation summaries that didn't land.
#
# Scans each per-owner conv.db for rows with summary IS NULL AND ts < now()-1h
# and re-enqueues a summarize-job. The summarizer worker picks them up on the
# next poll cycle.
set -uo pipefail

LOG_PREFIX="$(date -Iseconds) conv-summarize-backfill:"
echo "$LOG_PREFIX start"

CONV_ROOT="${FLYN_CONV_ROOT:-$HOME/.flyn/memory-router/conv}"
QUEUE_DIR="${FLYN_MEMORY_ROUTER_HOME:-$HOME/.flyn/memory-router}/queue/conv-summarize"
mkdir -p "$QUEUE_DIR"

if [ ! -d "$CONV_ROOT" ]; then
  echo "$LOG_PREFIX no conv root — skipping"
  exit 0
fi

# Find per-owner DBs (excluding owners.db)
shopt -s nullglob
for db in "$CONV_ROOT"/*.db; do
  [ -f "$db" ] || continue
  owner=$(basename "$db" .db)
  [ "$owner" = "owners" ] && continue

  DB_PATH="$db" OWNER="$owner" QUEUE_DIR="$QUEUE_DIR" python3 <<'PYEOF'
import sqlite3, json, time, os
from pathlib import Path
queue_dir = Path(os.environ["QUEUE_DIR"])
queue_dir.mkdir(parents=True, exist_ok=True)
db_path = os.environ["DB_PATH"]
owner = os.environ["OWNER"]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
rows = conn.execute(
    "SELECT id, body, sender_id FROM messages WHERE summary IS NULL AND ts < ?",
    (cutoff,)
).fetchall()
print(f"  {owner}: {len(rows)} pending")
for r in rows:
    job_path = queue_dir / f"conv-summarize-{owner}-{r['id']}.json"
    job_path.write_text(json.dumps({
        "owner_id": owner, "db_path": db_path, "row_id": r["id"],
        "body": r["body"], "sender_id": r["sender_id"],
    }))
PYEOF
done

echo "$LOG_PREFIX done"
