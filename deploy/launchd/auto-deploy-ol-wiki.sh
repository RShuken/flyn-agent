#!/bin/bash
# Auto-deploy OL explainer wiki to Cloudflare Pages.
# Runs every 3 min via launchd (ai.flyn.ol-wiki-autodeploy.plist).
#
# Approach: track the last-deployed SHA in a stamp file. On each tick:
#   1. Pull origin/main (rebase, non-destructive).
#   2. If current HEAD differs from stamp AND explainer/ changed since stamp,
#      deploy + update stamp. Otherwise no-op.
#
# This works whether the new commits came from 4C itself or from elsewhere.
#
# Logs: /tmp/ol-wiki-autodeploy.log
# Stamp: /tmp/ol-wiki-autodeploy.last-deployed-sha

set -euo pipefail

REPO="/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase"
LOG="/tmp/ol-wiki-autodeploy.log"
LOCK_DIR="/tmp/ol-wiki-autodeploy.lock.d"
STAMP="/tmp/ol-wiki-autodeploy.last-deployed-sha"
WRANGLER="/opt/homebrew/bin/wrangler"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG"; }

# Atomic lock via mkdir
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [ -d "$LOCK_DIR" ] && [ "$(find "$LOCK_DIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    log "stale lock; clearing"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    mkdir "$LOCK_DIR" 2>/dev/null || { log "still locked; skipping"; exit 0; }
  else
    exit 0  # silent skip; another tick has it
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

cd "$REPO" || { log "repo missing at $REPO"; exit 1; }

# Don't touch the working tree if there are local uncommitted changes
if ! git diff-index --quiet HEAD --; then
  log "uncommitted local changes; skipping"
  exit 0
fi

# Make sure we have latest
git fetch --quiet origin main || { log "git fetch failed"; exit 1; }

# Fast-forward / rebase to origin if behind
local_head=$(git rev-parse HEAD)
remote_head=$(git rev-parse origin/main)
if [ "$local_head" != "$remote_head" ]; then
  if ! git pull --rebase --quiet origin main; then
    log "git pull --rebase failed"
    exit 1
  fi
fi

current=$(git rev-parse HEAD)
last_deployed=$(cat "$STAMP" 2>/dev/null || echo "")

if [ "$current" = "$last_deployed" ]; then
  exit 0  # already up-to-date; silent
fi

# Decide: did explainer/ actually change since the last-deployed SHA?
deploy_reason=""
if [ -z "$last_deployed" ]; then
  deploy_reason="first run; no stamp"
elif ! git cat-file -e "$last_deployed^{commit}" 2>/dev/null; then
  deploy_reason="last-deployed SHA $last_deployed not in repo (rebase?); deploying current"
elif git diff --name-only "$last_deployed" "$current" -- explainer/ | grep -q .; then
  deploy_reason="explainer/ changed ($last_deployed..$current)"
else
  # No explainer/ change; just update stamp so we don't recheck every 3 min
  echo "$current" > "$STAMP"
  exit 0
fi

log "$deploy_reason"
if "$WRANGLER" pages deploy explainer \
     --project-name=ol-explainer-wiki \
     --branch=main \
     --commit-dirty=true >> "$LOG" 2>&1; then
  echo "$current" > "$STAMP"
  log "deploy ok ($current)"
else
  log "deploy FAILED"
  exit 1
fi
