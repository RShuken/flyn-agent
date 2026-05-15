#!/usr/bin/env bash
# Idempotent installer for flyn-memory-router on macOS / launchd.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/.flyn/memory-router"
PLIST="$HOME/Library/LaunchAgents/ai.flyn.memory-router.plist"

echo "==> Installing flyn-memory-router into $TARGET"

mkdir -p "$TARGET/data" "$TARGET/queue"

# copy code (skip dev-only artifacts)
rsync -a --delete \
  --exclude='.venv/' --exclude='__pycache__/' --exclude='.pytest_cache/' \
  --exclude='tests/' --exclude='*.egg-info/' \
  "$HERE/" "$TARGET/"

# python venv + install
if [ ! -d "$TARGET/.venv" ]; then
  python3 -m venv "$TARGET/.venv"
fi
"$TARGET/.venv/bin/pip" install --upgrade pip >/dev/null
if [ -f "$TARGET/requirements-lock.txt" ]; then
  "$TARGET/.venv/bin/pip" install -r "$TARGET/requirements-lock.txt"
else
  "$TARGET/.venv/bin/pip" install fastapi 'uvicorn[standard]' pydantic httpx slowapi
fi
"$TARGET/.venv/bin/pip" install -e "$TARGET"

# render plist
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|{{HOME}}|$HOME|g" "$HERE/ai.flyn.memory-router.plist.template" > "$PLIST"

# (re)load
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# wait for liveness
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sS http://127.0.0.1:8400/api/health 2>/dev/null | grep -q '"ok":true'; then
    echo "==> flyn-memory-router is live on :8400"
    exit 0
  fi
  sleep 1
done

echo "ERROR: flyn-memory-router did not become healthy. Check /tmp/flyn-memory-router.log" >&2
tail -20 /tmp/flyn-memory-router.log >&2 2>/dev/null || true
exit 1
