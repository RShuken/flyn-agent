---
name: claude-p-stream-json-needs-verbose
description: When invoking `claude -p` with `--output-format stream-json`, you MUST also pass `--verbose` or claude exits immediately with "When using --print, --output-format=stream-json requires --verbose" and writes nothing to stdout.
type: reference
---

# `claude -p --output-format stream-json` requires `--verbose`

Discovered 2026-05-15 03:08 during the Phase 1 MVP ship-gate. The Phase 1 plan's `ClaudePBackend._build_command()` originally produced:

```python
[CLAUDE_BIN, "-p", prompt,
 "--output-format", "stream-json",
 "--max-turns", str(spec.max_turns),
 "--dangerously-skip-permissions"]
```

When this ran, `claude` exited with `Error: When using --print, --output-format=stream-json requires --verbose` and wrote 0 bytes to stdout. The orchestrator captured the empty stream, the reviewer captured an empty diff, both jsonl files were 0 bytes, and the task happily transitioned to `deliverable_ready` because no exception was raised — but no actual work happened.

## The fix

Add `--verbose` to the command:

```python
[CLAUDE_BIN, "-p", prompt,
 "--output-format", "stream-json",
 "--verbose",                       # REQUIRED with stream-json
 "--max-turns", str(spec.max_turns),
 "--dangerously-skip-permissions"]
```

## Why this took 3 attempts to debug

1. **First attempt failed** because launchd `rsync --delete` in `install.sh` blew away the seeded test-repo. Fix: `--exclude='test-repo/'`. (Bug #1)
2. **Second attempt failed** because the launchd plist's `PATH` didn't include `/Users/4c/.local/bin/` where the real `claude` binary lives — Popen got `FileNotFoundError: 'claude'`. Fix: add `$HOME/.local/bin` to the plist PATH + add `CLAUDE_BIN` and `HOME` envs. (Bug #2)
3. **Third attempt failed** because `--verbose` was missing — claude wrote nothing, both jsonl files were 0 bytes, but the orchestrator silently marked the task `deliverable_ready` since no exception was raised. (Bug #3 — THIS lesson.)

The reviewer's eventual passed=true was a false positive — it was reviewing an empty diff and (correctly per its prompt) seeing no issues. **Two systemic defenses Phase 1b should add:**

- The dispatcher should check `result.exit_code != 0` and refuse to advance to `reviewed`.
- The reviewer should refuse to pass if the diff is empty (treat empty-diff as `passed=False, severity=critical, area=correctness, note="builder produced no diff"`).

## How to detect this same class of bug in the future

After a worker run, before advancing state:
```python
capture_size = result.capture_path.stat().st_size
if capture_size < 100:  # less than ~5 events = something went wrong
    raise WorkerEmittedNothing(f"capture file {result.capture_path} is {capture_size} bytes — check the command builder")
```

Wire this into the dispatcher in Phase 1b.

## Cost data captured during this run

- Builder run (real work): ~$0.30 (one turn, ~25k tokens including cache)
- Reviewer run (fresh-context per spec §2): ~$0.15 (one turn, 24k cache-read + 22k cache-write)
- Total per round-trip: ~$0.45 USD on Claude Sonnet 4.6 (via subscription, marginal cost was $0)

Within the documented expectations from the orchestration research report — Anthropic's C-compiler paper hit ~$10/session, this MVP is well under that.
