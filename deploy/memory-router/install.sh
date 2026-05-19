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

# --- Read-side install steps (Task 40) ---

if [[ -d /usr/local/bin && -w /usr/local/bin ]]; then
  ln -sf "$TARGET/.venv/bin/flyn-mem" /usr/local/bin/flyn-mem
  echo "  ✓ symlinked /usr/local/bin/flyn-mem -> $TARGET/.venv/bin/flyn-mem"
elif sudo -n true 2>/dev/null; then
  sudo ln -sf "$TARGET/.venv/bin/flyn-mem" /usr/local/bin/flyn-mem
  echo "  ✓ symlinked /usr/local/bin/flyn-mem (via sudo)"
else
  echo "  ! cannot symlink /usr/local/bin/flyn-mem (no passwordless sudo)"
  echo "    Run manually:  sudo ln -sf $TARGET/.venv/bin/flyn-mem /usr/local/bin/flyn-mem"
fi

"$TARGET/.venv/bin/python" - <<'PYEOF'
from pathlib import Path
import os
from flyn_memory_router.discovery import (
    write_auto_memory_pointer, append_memory_md_index, append_tools_md
)

automem = Path(os.environ.get("FLYN_AUTO_MEMORY_DIR",
                              str(Path.home() / ".claude" / "projects" /
                                  "-Users-4c-AI" / "memory")))
workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                str(Path.home() / ".openclaw" / "workspace")))

write_auto_memory_pointer(automem)
append_memory_md_index(automem)
append_tools_md(workspace)
print(f"  ✓ auto-memory pointer at {automem}/feedback_memory_router.md")
print(f"  ✓ TOOLS.md updated at   {workspace}/TOOLS.md")
PYEOF

# render plist
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|{{HOME}}|$HOME|g" "$HERE/ai.flyn.memory-router.plist.template" > "$PLIST"

# (re)load
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# --- Conversation tier (Telegram slice 1) ---
CONV_ROOT="${FLYN_CONV_ROOT:-$HOME/.flyn/memory-router/conv}"
mkdir -p "$CONV_ROOT"
echo "  ✓ conv root at $CONV_ROOT"

# Seed principals.json with the current user as Ryan if missing
if [ ! -f "$CONV_ROOT/principals.json" ]; then
  cat > "$CONV_ROOT/principals.json" <<JSON
{
  "owners": [
    {
      "id": "ryan",
      "display_name": "Ryan Shuken",
      "principals": {
        "telegram": "7191564227"
      }
    }
  ]
}
JSON
  echo "  ✓ seeded conv principals.json (edit to add Beth/Eric/etc later)"
fi

# Install the OpenClaw hook script
HOOK_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/hooks/flyn-conv-memory-tap.sh"
if [ -f "$HOOK_SRC" ]; then
  HOOK_DST="$HOME/.openclaw/hooks/flyn-conv-memory-tap.sh"
  mkdir -p "$(dirname "$HOOK_DST")"
  install -m 755 "$HOOK_SRC" "$HOOK_DST"
  echo "  ✓ openclaw hook installed at $HOOK_DST"
  echo "    NOTE: register this hook in ~/.openclaw/openclaw.json under hooks.internal.entries"
fi

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
