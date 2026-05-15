#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${ENDPOINT:-http://127.0.0.1:8200/api/meetings/krisp}"
TOKEN="${FLYN_KRISP_TOKEN:?set FLYN_KRISP_TOKEN to your shared secret}"
FIXTURE="${1:-$(dirname "$0")/fixtures/krisp_sample.json}"

echo "→ POST $ENDPOINT  (fixture: $FIXTURE)"
RESP="$(curl -sS -w '\n---HTTP %{http_code}---\n' -X POST "$ENDPOINT" \
  -H "X-OL-Krisp-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  --data @"$FIXTURE")"
echo "$RESP"

echo "→ Inspecting DB"
sqlite3 "${FLYN_MEETINGS_DB:-$HOME/.openclaw/data/flyn-meetings.db}" -header -column \
  "SELECT meeting_id, title, status, classifier_confidence FROM meetings ORDER BY first_seen_at DESC LIMIT 5"
