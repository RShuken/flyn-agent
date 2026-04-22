#!/usr/bin/env bash
# install-flyn.sh — idempotent end-to-end deploy for Flyn on Apple Silicon.
#
# Deploys:
#   - Ollama + gemma4:e4b local heartbeat model
#   - Neo4j 5.26 in Docker (flyn-neo4j, 1GB heap)
#   - graphiti-core Python venv + Flask REST wrapper on :8100
#   - launchd service ai.flyn.graphiti-api (auto-start + auto-restart)
#   - Lossless Claw plugin (context engine)
#   - OpenClaw config: heartbeat model, embedding provider, memorySearch routing
#   - workspace/*.md files into ~/.openclaw/workspace/
#
# Reads secrets from (or writes to) ~/.openclaw/agents/main/agent/auth-profiles.json.
# Designed for the reader who's just read POSTMORTEM-2026-04-21.md.
#
# Usage:
#   ./install-flyn.sh              # interactive, prompts for Gemini key if missing
#   ./install-flyn.sh --check      # dry-run state check, no mutations
#   ./install-flyn.sh --force      # re-run everything, safe (idempotent)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OC_ROOT="$HOME/.openclaw"
OC_WORKSPACE="$OC_ROOT/workspace"
OC_AGENT="$OC_ROOT/agents/main/agent"
AUTH_FILE="$OC_AGENT/auth-profiles.json"
KG_DIR="$OC_WORKSPACE/kg"
STRUCTURED_DIR="$OC_WORKSPACE/memory/structured"
VENV_DIR="$STRUCTURED_DIR/graphiti-venv"
PLIST_DEST="$HOME/Library/LaunchAgents/ai.flyn.graphiti-api.plist"
NEO4J_CONTAINER="flyn-neo4j"
NEO4J_DATA_DIR="$STRUCTURED_DIR/neo4j/data"
NEO4J_LOGS_DIR="$STRUCTURED_DIR/neo4j/logs"

MODE="install"
[[ "${1:-}" == "--check" ]] && MODE="check"
[[ "${1:-}" == "--force" ]] && MODE="force"

log()  { printf "\033[1;34m▶\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗\033[0m %s\n" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "required: $1"; }

# --- 0. prereqs ---
log "0. Prerequisites"
need openclaw
need ollama
need docker
need python3
need tmux
need brew
need curl

OC_VERSION="$(openclaw --version 2>&1 | head -1)"
ok "OpenClaw: $OC_VERSION"

[[ "$MODE" == "check" ]] && { warn "check mode — exiting after prereqs"; exit 0; }

# --- 1. Ollama + gemma4:e4b ---
log "1. Ollama service + gemma4:e4b"
brew services list | grep -q "^ollama.*started" || brew services start ollama
sleep 2
if ollama list 2>/dev/null | grep -q "gemma4:e4b"; then
  ok "gemma4:e4b already pulled"
else
  log "pulling gemma4:e4b (9.6 GB, this takes 2-5 min)"
  tmux new-session -d -s flyn-g4-pull "ollama pull gemma4:e4b; touch /tmp/flyn-g4-pull.done"
  until [[ -f /tmp/flyn-g4-pull.done ]]; do sleep 10; done
  rm /tmp/flyn-g4-pull.done
  ok "gemma4:e4b pulled"
fi

# --- 2. auth-profiles.json bootstrap ---
log "2. Auth profiles (ollama + neo4j + gemini)"
[[ -f "$AUTH_FILE" ]] || die "auth-profiles.json missing at $AUTH_FILE — run OpenClaw install first"

python3 - <<PY
import json, os, sys
p = os.path.expanduser("$AUTH_FILE")
d = json.load(open(p))
changed = False

# ollama:default — even local providers need this entry
if "ollama:default" not in d["profiles"]:
    d["profiles"]["ollama:default"] = {"type":"token","provider":"ollama","token":"local"}
    changed = True
    print("  + ollama:default")

if changed:
    tmp = p + ".tmp"
    with open(tmp,"w") as f: json.dump(d, f, indent=4)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    print("  auth-profiles.json updated")
else:
    print("  (no ollama:default change)")
PY

# Gemini key — prompt if missing
HAS_GEMINI=$(python3 -c "import json; d=json.load(open('$AUTH_FILE')); print('yes' if 'google:default' in d['profiles'] else 'no')")
if [[ "$HAS_GEMINI" != "yes" ]]; then
  echo
  warn "Gemini API key needed for embeddings. Get one at https://aistudio.google.com/app/apikey"
  read -rsp "Paste Gemini API key (or enter to skip): " GEMINI_KEY
  echo
  if [[ -n "$GEMINI_KEY" ]]; then
    GEMINI_KEY="$GEMINI_KEY" python3 - <<'PY'
import json, os
p = os.path.expanduser(os.environ.get("AUTH_FILE","~/.openclaw/agents/main/agent/auth-profiles.json"))
d = json.load(open(p))
k = os.environ["GEMINI_KEY"]
d["profiles"]["gemini:default"] = {"type":"token","provider":"gemini","token":k}
d["profiles"]["google:default"] = {"type":"token","provider":"google","token":k}
tmp = p + ".tmp"
with open(tmp,"w") as f: json.dump(d, f, indent=4)
os.chmod(tmp, 0o600)
os.replace(tmp, p)
print("  + gemini:default + google:default")
PY
  else
    warn "skipped; Graphiti embeddings will fail until you add a key"
  fi
else
  ok "gemini:default + google:default already set"
fi

# --- 3. Neo4j Docker ---
log "3. Neo4j 5.26 in Docker ($NEO4J_CONTAINER)"
mkdir -p "$NEO4J_DATA_DIR" "$NEO4J_LOGS_DIR"
if docker ps --filter "name=$NEO4J_CONTAINER" --format '{{.Names}}' | grep -q "$NEO4J_CONTAINER"; then
  ok "$NEO4J_CONTAINER already running"
else
  if docker ps -a --filter "name=$NEO4J_CONTAINER" --format '{{.Names}}' | grep -q "$NEO4J_CONTAINER"; then
    docker start "$NEO4J_CONTAINER" && ok "existing $NEO4J_CONTAINER container started"
  else
    NEO4J_PASS="$(openssl rand -base64 24 | tr -d '/+=')"
    docker pull neo4j:5.26 >/dev/null
    docker run -d \
      --name "$NEO4J_CONTAINER" \
      --restart unless-stopped \
      -p 127.0.0.1:7474:7474 -p 127.0.0.1:7687:7687 \
      -v "$NEO4J_DATA_DIR":/data \
      -v "$NEO4J_LOGS_DIR":/logs \
      -e NEO4J_AUTH="neo4j/$NEO4J_PASS" \
      -e NEO4J_server_memory_heap_initial__size=512m \
      -e NEO4J_server_memory_heap_max__size=1G \
      -e NEO4J_server_memory_pagecache_size=256m \
      neo4j:5.26 >/dev/null
    # wait for bolt
    for i in 1 2 3 4 5 6; do
      sleep 5
      docker exec "$NEO4J_CONTAINER" bash -c "cypher-shell -u neo4j -p \"$NEO4J_PASS\" 'RETURN 1'" >/dev/null 2>&1 && break
    done
    # store in auth-profiles.json
    NEO4J_PASS="$NEO4J_PASS" python3 - <<'PY'
import json, os
p = os.path.expanduser(os.environ.get("AUTH_FILE","~/.openclaw/agents/main/agent/auth-profiles.json"))
d = json.load(open(p))
d["profiles"]["neo4j:default"] = {
    "type":"token","provider":"neo4j","user":"neo4j",
    "token":os.environ["NEO4J_PASS"],"uri":"bolt://localhost:7687"
}
tmp = p + ".tmp"
with open(tmp,"w") as f: json.dump(d, f, indent=4)
os.chmod(tmp, 0o600)
os.replace(tmp, p)
print("  + neo4j:default")
PY
    ok "Neo4j started + password stored"
  fi
fi

# --- 4. Graphiti Python venv ---
log "4. Graphiti Python venv + graphiti-core[google-genai] + flask"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
# Prefer pinned lock file for reproducibility; fall back to unpinned install
# so a fresh clone without a lock file still works.
if [[ -f "$REPO_ROOT/deploy/kg/requirements-lock.txt" ]]; then
  "$VENV_DIR/bin/pip" install -r "$REPO_ROOT/deploy/kg/requirements-lock.txt" >/dev/null
else
  "$VENV_DIR/bin/pip" install "graphiti-core[google-genai]>=0.28.2" flask >/dev/null
fi
ok "venv + deps ready"

# --- 5. REST wrapper + launchd ---
log "5. flyn-graphiti-api.py + launchd plist"
mkdir -p "$KG_DIR"
cp -f "$REPO_ROOT/deploy/kg/flyn-graphiti-api.py" "$KG_DIR/flyn-graphiti-api.py"

sed "s|{{HOME}}|$HOME|g" "$REPO_ROOT/deploy/launchd/ai.flyn.graphiti-api.plist.template" > "$PLIST_DEST"

# unload + reload for idempotency
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"
sleep 30  # first run needs Graphiti to build indices

if curl -sSf http://localhost:8100/api/health | grep -q '"status":"ok"'; then
  ok "flyn-graphiti-api up on :8100"
else
  warn "flyn-graphiti-api not healthy yet — check tail /tmp/flyn-graphiti-api.log"
fi

# --- 6. Lossless Claw plugin ---
log "6. Lossless Claw plugin (Martian-Engineering)"
if openclaw plugins list 2>/dev/null | grep -q "lossless-claw"; then
  ok "lossless-claw already installed"
else
  openclaw plugins install @martian-engineering/lossless-claw
  ok "lossless-claw installed"
fi

# --- 7. OpenClaw config (additive, via config set) ---
log "7. OpenClaw config — heartbeat + memorySearch"
openclaw config set agents.defaults.heartbeat.model "ollama/gemma4:e4b"
openclaw config set agents.defaults.heartbeat.isolatedSession true --strict-json
openclaw config set agents.defaults.heartbeat.suppressToolErrorWarnings true --strict-json
openclaw config set "agents.defaults.models.ollama/gemma4:e4b" '{}' --strict-json
openclaw config set agents.defaults.memorySearch.provider gemini
openclaw config set agents.defaults.memorySearch.fallback local
# OpenClaw memory search uses gemini-embedding-2-preview (MTEB-leading multimodal).
# Graphiti's REST wrapper independently uses gemini-embedding-001 (stable) — see
# deploy/kg/flyn-graphiti-api.py. This two-embedder split is intentional.
openclaw config set agents.defaults.memorySearch.model gemini-embedding-2-preview
openclaw config validate && ok "config valid"

# --- 8. workspace files ---
log "8. Deploy workspace/*.md"
rsync -a "$REPO_ROOT/workspace/" "$OC_WORKSPACE/"
ok "workspace files synced"

# --- 9. Register launchd pulses (heartbeats + warm-at-boot) ---
log "9. Register launchd pulses"
"$REPO_ROOT/deploy/cron/register-flyn-crons.sh"
ok "launchd pulses registered"

# --- 10. Gateway restart ---
log "10. Gateway restart"
launchctl kickstart -k "gui/$(id -u)/ai.openclaw.gateway" || die "failed to restart gateway"
sleep 6
openclaw health | head -6 || true

# --- Done ---
echo
ok "Flyn install complete."
echo
echo "Next steps:"
echo "  - Process BOOTSTRAP.md on Flyn's first session"
echo "  - Seed initial facts into Graphiti via curl → POST /api/episode"
echo
echo "Verify:"
echo "  openclaw health"
echo "  curl -s http://localhost:8100/api/health"
echo "  docker ps --filter name=$NEO4J_CONTAINER"
echo "  ollama list | grep gemma4"
