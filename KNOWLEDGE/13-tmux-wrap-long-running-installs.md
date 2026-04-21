---
name: oac-tmux-long-running
description: Use tmux-detached sessions for OAC remote commands that exceed the ~10-min PTY exec timeout (ollama pull, brew upgrade, npm install, git clone, etc.)
type: feedback
originSessionId: 0eaf92c3-b027-400b-b564-f087026dba75
---
## OAC remote exec has an implicit ~10-min PTY timeout

Any command that runs longer than ~10 minutes gets killed with exit code 124 (SIGTERM). This includes:
- `ollama pull` for large models (Gemma 4 e2b is 7.2GB — hits the wall easily)
- `brew upgrade` of big apps
- `npm install` in fresh projects
- Big git clones
- Large file downloads

**Why:** Discovered fixing Ian Ferguson's Gemma 4 install on 2026-04-18 — tried pulling gemma4:e4b (4.5GB) twice, both hit exit=124 at the PTY timeout. tmux-detached pattern was the fix.

**How to apply:** Wrap long-running commands in a tmux session:

```bash
# Start detached tmux session — returns immediately
tmux new-session -d -s <name> '<command>'

# Check if still running
tmux has-session -t <name> 2>/dev/null && echo STILL_RUNNING || echo DONE

# See current output/progress
tmux capture-pane -t <name> -p | tail -10

# See scrollback (last 50 lines)
tmux capture-pane -t <name> -p -S -50 | tail -20

# Kill if needed
tmux kill-session -t <name>
```

**Concrete example (what worked for Ian):**
```bash
tmux kill-session -t gpull 2>/dev/null
tmux new-session -d -s gpull ollama pull gemma4:e2b
# Wait, then later:
tmux capture-pane -t gpull -p -S -50 | tail -15
# Shows: "pulling 4e30e2665218: 100% ... verifying sha256 digest"
ollama list  # confirms model is committed after verify finishes
```

**Tmux is pre-installed** on Homebrew Macs at `/opt/homebrew/bin/tmux`. Verify with `which tmux` before assuming.

**Capture-pane nuance:** `tmux capture-pane -p` shows the currently VISIBLE pane content. If the command uses ANSI cursor-position escapes to overwrite the same line (like ollama's progress bar), the pane may appear empty or have stale content. Use `-S -50` to grab 50 lines of scrollback for better visibility into past progress messages.

**Completion detection:** When `tmux has-session -t <name>` returns non-zero (exit 1), the tmux server has reaped the session — that's the signal the command finished and its shell exited. Combine with a sanity check (`ollama list`, file existence, etc.) to verify success vs failure.

**Cross-reference:** `project_ian_ferguson_install_state.md` for the full install-timeline context; `feedback_oac_command_line_limit.md` for the related ~1000-char command-length limit.
