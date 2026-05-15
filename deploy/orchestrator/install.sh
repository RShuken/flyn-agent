#!/usr/bin/env bash
# Idempotent installer for flyn-orchestrator on macOS / launchd.
# Mirrors the deploy/memory-router/install.sh pattern.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/.flyn/orchestrator"
PLIST="$HOME/Library/LaunchAgents/ai.flyn.orchestrator.plist"

echo "==> Installing flyn-orchestrator into $TARGET"

mkdir -p "$TARGET/data" "$TARGET/workspaces" "$TARGET/captures" "$TARGET/coordination"

# Set up the local test repo (empty git repo) so the orchestrator has a default workspace to allocate worktrees from
if [ ! -d "$TARGET/test-repo/.git" ]; then
  echo "==> Initializing test-repo at $TARGET/test-repo"
  mkdir -p "$TARGET/test-repo"
  ( cd "$TARGET/test-repo" && git init -b main -q && \
    git config user.email "flyn@getcora.io" && \
    git config user.name "Flyn" && \
    echo "Flyn test repo for orchestrator MVP." > README.md && \
    git add . && git commit -q -m "seed" )
fi

# rsync code into target, excluding dev artifacts
rsync -a --delete \
  --exclude='.venv/' --exclude='__pycache__/' --exclude='.pytest_cache/' \
  --exclude='tests/' --exclude='*.egg-info/' --exclude='test-repo/' \
  "$HERE/" "$TARGET/"

# python venv + install
if [ ! -d "$TARGET/.venv" ]; then
  python3 -m venv "$TARGET/.venv"
fi
"$TARGET/.venv/bin/pip" install --upgrade pip >/dev/null
if [ -f "$TARGET/requirements-lock.txt" ]; then
  "$TARGET/.venv/bin/pip" install -r "$TARGET/requirements-lock.txt"
else
  "$TARGET/.venv/bin/pip" install fastapi 'uvicorn[standard]' pydantic httpx
fi
"$TARGET/.venv/bin/pip" install -e "$TARGET"

# render plist
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|{{HOME}}|$HOME|g" "$HERE/ai.flyn.orchestrator.plist.template" > "$PLIST"

# (re)load
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# wait for liveness
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sS http://127.0.0.1:8300/api/health 2>/dev/null | grep -q '"ok":true'; then
    echo "==> flyn-orchestrator is live on :8300"
    exit 0
  fi
  sleep 1
done

echo "ERROR: flyn-orchestrator did not become healthy. Check /tmp/flyn-orchestrator.log" >&2
tail -20 /tmp/flyn-orchestrator.log >&2 2>/dev/null || true
exit 1
