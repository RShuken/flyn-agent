---
name: feedback-ssh-mac-mini-commands
description: SSH commands to Mac Mini need login shell wrapper and correct .local hostname
type: feedback
---

When running commands on the Mac Mini over SSH, always use `zsh -l -c "..."` wrapper.

**Why:** Non-interactive SSH sessions on macOS don't source `~/.zprofile`, so Homebrew's `/opt/homebrew/bin` isn't on PATH. Node, npm, npx, and wrangler all fail with "command not found" without the login shell wrapper. Also: the mDNS hostname is `4cs-Mac-mini.local` (`.local`, NOT `.lan`) — using `.lan` causes host key verification failure.

**How to apply:**
- Always: `ssh -i ~/.ssh/mac-mini 4c@4cs-Mac-mini.local 'zsh -l -c "cd /path && command"'`
- Never: `ssh ... 4c@4cs-Mac-mini.lan` (wrong suffix)
- Never: `ssh ... "node ..."` without the `zsh -l -c` wrapper
- Shell quoting with `===` breaks in zsh `-c` (interpreted as comparison operator) — use plain labels instead
