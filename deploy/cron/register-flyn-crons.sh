#!/usr/bin/env bash
# Register all 5 Flyn heartbeat pulses as macOS launchd agents.
#
# Why launchd instead of `crontab` or `openclaw cron add`:
#   - crontab: modifying from a non-TTY SSH session requires Full Disk Access
#     permission on macOS 10.15+, which needs a GUI trip to System Settings.
#   - openclaw cron add: triggers agent turns (Codex API call per fire);
#     these pulses are shell scripts that don't need agent intermediation.
#   - launchd: works from SSH, Mac-native, auto-starts at boot, per-agent logs.
#
# Idempotent: safe to re-run. Replaces existing plists of the same Label.

set -euo pipefail

SCRIPTS_DIR="${HOME}/.openclaw/scripts/flyn"
LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${HOME}/.openclaw/logs"
UID_NUM="$(id -u)"

log() { printf "\033[1;34m▶\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m✓\033[0m %s\n" "$*"; }

mkdir -p "${SCRIPTS_DIR}" "${LAUNCH_DIR}" "${LOG_DIR}"

# 1. Install scripts
log "installing scripts to ${SCRIPTS_DIR}"
cp -f "$(dirname "$0")/scripts/"*.sh "${SCRIPTS_DIR}/"
chmod +x "${SCRIPTS_DIR}/"*.sh

# Helper: write a launchd plist with one StartCalendarInterval dict
#   write_plist_once <label> <script-name> <hour> <minute> [weekday]
write_plist_once() {
  local label="$1" script="$2" hour="$3" minute="$4" weekday="${5:-}"
  local plist="${LAUNCH_DIR}/${label}.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0">'
    echo '<dict>'
    echo "  <key>Label</key><string>${label}</string>"
    echo '  <key>ProgramArguments</key>'
    echo "  <array>"
    echo "    <string>/bin/bash</string>"
    echo "    <string>${SCRIPTS_DIR}/${script}</string>"
    echo "  </array>"
    echo '  <key>EnvironmentVariables</key><dict>'
    echo "    <key>HOME</key><string>${HOME}</string>"
    echo "    <key>PATH</key><string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>"
    echo "  </dict>"
    echo '  <key>StartCalendarInterval</key>'
    echo '  <dict>'
    echo "    <key>Hour</key><integer>${hour}</integer>"
    echo "    <key>Minute</key><integer>${minute}</integer>"
    [ -n "$weekday" ] && echo "    <key>Weekday</key><integer>${weekday}</integer>"
    echo '  </dict>'
    echo "  <key>StandardOutPath</key><string>${LOG_DIR}/cron-${label}.log</string>"
    echo "  <key>StandardErrorPath</key><string>${LOG_DIR}/cron-${label}.err</string>"
    echo '</dict>'
    echo '</plist>'
  } > "$plist"
  launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/${UID_NUM}" "$plist"
  ok "${label} (hr=${hour} min=${minute} wday=${weekday:-any})"
}

# Helper: write a plist with MULTIPLE StartCalendarInterval dicts (an array)
# used for the hourly 06-23 memory-autosave pulse.
write_plist_hourly_range() {
  local label="$1" script="$2" h_start="$3" h_end="$4"
  local plist="${LAUNCH_DIR}/${label}.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0">'
    echo '<dict>'
    echo "  <key>Label</key><string>${label}</string>"
    echo '  <key>ProgramArguments</key><array>'
    echo "    <string>/bin/bash</string><string>${SCRIPTS_DIR}/${script}</string>"
    echo '  </array>'
    echo '  <key>EnvironmentVariables</key><dict>'
    echo "    <key>HOME</key><string>${HOME}</string>"
    echo "    <key>PATH</key><string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>"
    echo "  </dict>"
    echo '  <key>StartCalendarInterval</key>'
    echo '  <array>'
    local h
    for ((h=h_start; h<=h_end; h++)); do
      echo '    <dict>'
      echo "      <key>Hour</key><integer>${h}</integer>"
      echo '      <key>Minute</key><integer>0</integer>'
      echo '    </dict>'
    done
    echo '  </array>'
    echo "  <key>StandardOutPath</key><string>${LOG_DIR}/cron-${label}.log</string>"
    echo "  <key>StandardErrorPath</key><string>${LOG_DIR}/cron-${label}.err</string>"
    echo '</dict></plist>'
  } > "$plist"
  launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/${UID_NUM}" "$plist"
  ok "${label} (hours ${h_start}-${h_end}:00)"
}

# Helper: plist with array of StartCalendarInterval across weekdays (for morning-digest)
write_plist_weekdays() {
  local label="$1" script="$2" hour="$3" minute="$4"
  local plist="${LAUNCH_DIR}/${label}.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>${label}</string>"
    echo '  <key>ProgramArguments</key><array>'
    echo "    <string>/bin/bash</string><string>${SCRIPTS_DIR}/${script}</string>"
    echo '  </array>'
    echo '  <key>EnvironmentVariables</key><dict>'
    echo "    <key>HOME</key><string>${HOME}</string>"
    echo "    <key>PATH</key><string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>"
    echo '  </dict>'
    echo '  <key>StartCalendarInterval</key><array>'
    local d
    for d in 1 2 3 4 5; do
      echo '    <dict>'
      echo "      <key>Hour</key><integer>${hour}</integer>"
      echo "      <key>Minute</key><integer>${minute}</integer>"
      echo "      <key>Weekday</key><integer>${d}</integer>"
      echo '    </dict>'
    done
    echo '  </array>'
    echo "  <key>StandardOutPath</key><string>${LOG_DIR}/cron-${label}.log</string>"
    echo "  <key>StandardErrorPath</key><string>${LOG_DIR}/cron-${label}.err</string>"
    echo '</dict></plist>'
  } > "$plist"
  launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/${UID_NUM}" "$plist"
  ok "${label} (weekdays ${hour}:${minute})"
}

# 2. Register each pulse
log "registering 5 flyn pulses as launchd agents"
write_plist_weekdays     "ai.flyn.pulse.morning-digest"   "morning-digest.sh"   7  0
write_plist_hourly_range "ai.flyn.pulse.memory-autosave"  "memory-autosave.sh"  6 23
write_plist_once         "ai.flyn.pulse.health-check"     "health-check.sh"    22  0
write_plist_once         "ai.flyn.pulse.memory-rollup"    "memory-rollup.sh"   20  0  0   # Sunday
write_plist_once         "ai.flyn.pulse.model-drift"      "model-drift.sh"     21  0  0   # Sunday

echo
log "loaded launchd agents:"
launchctl list | awk '/ai\.flyn\.pulse/{print "  " $3}'

echo
ok "registration complete. Logs under ${LOG_DIR}/cron-<label>.{log,err}"
