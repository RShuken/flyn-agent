#!/usr/bin/env bash
# Pulse: nightly-backup
# Runs daily 02:00 local. Tars the SQLite + Neo4j data, copies to a local
# backups dir + (optionally) uploads to Drive. Idempotent.
#
# Backed up:
#   - ~/.openclaw/data/ol-pm.db         — wiki backend state (questions, decisions, audit, webhooks)
#   - ~/.openclaw/workspace/memory/structured/neo4j/data/  — Graphiti KG
#   - ~/.openclaw/projects/                — per-project configs
#   - ~/.openclaw/agents/main/sessions/    — session state
#
# NOT backed up (intentional — auth lives in profiles, not state):
#   - auth-profiles.json (sensitive; backup via separate encrypted path)

set -euo pipefail
PULSE_NAME="nightly-backup"
TS=$(date +%Y-%m-%d_%H%M%S)
BACKUP_ROOT="${HOME}/.openclaw/backups"
LOG="${HOME}/.openclaw/logs/${PULSE_NAME}-$(date +%Y-%m-%d).log"
mkdir -p "$BACKUP_ROOT" "$(dirname "$LOG")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG"; }

log "start"

# 1. SQLite — copy with WAL checkpoint so we get a consistent snapshot
OL_DB="${HOME}/.openclaw/data/ol-pm.db"
if [ -f "$OL_DB" ]; then
  sqlite3 "$OL_DB" "PRAGMA wal_checkpoint(FULL);" >/dev/null 2>&1 || log "wal_checkpoint nonfatal err"
  cp "$OL_DB" "/tmp/ol-pm.db.snap"
  log "sqlite snap ok ($(stat -f%z "$OL_DB" 2>/dev/null) bytes)"
else
  log "sqlite db missing at $OL_DB"
fi

# 2. Tarball
ARCHIVE="$BACKUP_ROOT/flyn-state-${TS}.tar.gz"
tar -czf "$ARCHIVE" \
  -C "$HOME/.openclaw" \
    data/ol-pm.db \
    workspace/memory/structured/neo4j/data \
    projects \
    agents/main/sessions \
  2>>"$LOG" || log "tar exited nonzero (some files may not exist yet)"

rm -f /tmp/ol-pm.db.snap

SIZE=$(stat -f%z "$ARCHIVE" 2>/dev/null || stat -c%s "$ARCHIVE" 2>/dev/null)
log "archive ok: $ARCHIVE ($SIZE bytes)"

# 3. Retention — keep 14 days
find "$BACKUP_ROOT" -name "flyn-state-*.tar.gz" -mtime +14 -delete 2>>"$LOG" || true
log "retention pruned"

# 4. (Optional) Drive upload — TODO when MCP session reliability is solved
# rclone copy "$ARCHIVE" gdrive:flyn-backups/ >>"$LOG" 2>&1 || log "rclone skipped"

log "done"
