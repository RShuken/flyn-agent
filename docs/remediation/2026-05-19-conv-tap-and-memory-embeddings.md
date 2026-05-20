# Remediation: conv-tap wiring + openclaw memory embeddings

**Date:** 2026-05-19
**Diagnosed by:** Claude Opus 4.7 (post-stability-sweep)
**Status:** Open

After shipping the conv-memory PR (#37) and 5 hardening fixes (F1–F5), a
live manual test session by Ryan revealed two unrelated issues that prevent
the system from working end-to-end as designed. This document captures
root cause, fix options, and recommended order.

---

## Issue A — Conv-memory tap hook installed but never invoked

### Symptom

User-triggered Telegram messages reach Flyn, get codex replies, but
never land in `~/.flyn/memory-router/conv/owners/ryan.db`. Searching
for any phrase from the conversation via `flyn-mem query` returns no
conv hit. The D1 ship-gate rubric's 6 live-smoke items cannot pass.

### Verified evidence

```
$ sqlite3 ~/.flyn/memory-router/conv/owners/ryan.db "select count(*) from messages"
Error: unable to open database "...": unable to open database file
```

The directory itself doesn't exist — no DB has ever been created for
the owner because no `conv_write_adapter` ingest has ever fired.

Gateway log shows every inbound message routed through the codex
harness directly, with no call to the conv-tap hook in between.

### Root cause

`deploy/memory-router/install.sh` ends with this message:

```
✓ openclaw hook installed at /Users/4c/.openclaw/hooks/flyn-conv-memory-tap.sh
  NOTE: register this hook in ~/.openclaw/openclaw.json under hooks.internal.entries
```

The script intentionally does NOT auto-edit `openclaw.json` (touching
that file under another process's watch is risky). The manual
registration step was forgotten when the conv-memory PR shipped.

### Fix steps

1. Read `~/.openclaw/openclaw.json`. Locate or create the
   `hooks.internal.entries` section.
2. Determine the correct trigger name for openclaw 2026.5.18 (the
   trigger taxonomy changed across openclaw versions — likely
   `message:received` or `channel:message_in`).
3. Add an entry pointing at
   `/Users/4c/.openclaw/hooks/flyn-conv-memory-tap.sh`. The hook
   reads JSON from stdin and POSTs to memory-router's
   `/api/memory/ingest` with `event_type=conversation_message`.
4. openclaw watches its config file via the reload subsystem
   (`config change detected` lines appear in gateway log); no
   manual restart should be needed, but a `launchctl kickstart`
   is a safe belt-and-suspenders.
5. Send a Telegram message → verify `ryan.db` appears within
   ~5 seconds with one row.
6. Wait 30 seconds → verify the summary column populates (Ollama
   call completes) and a corresponding Graphiti episode lands under
   `group_id=flyn-ryan`.

### Risk

**Low.** Additive change to an existing config section. Worst case:
trigger name is wrong → hook never fires → behavior is exactly as
today (broken in the same way). No path to break inbound delivery
or codex routing.

### Verification commands

```bash
# Before: should error or return 0
sqlite3 ~/.flyn/memory-router/conv/owners/ryan.db "select count(*) from messages"

# Send Telegram message containing 'remediation-test-2026' from phone

# After 5s: should return 1+
sqlite3 ~/.flyn/memory-router/conv/owners/ryan.db "select count(*) from messages"

# After 30s: summary populated
sqlite3 ~/.flyn/memory-router/conv/owners/ryan.db \
  "select id, summary IS NOT NULL as has_summary from messages order by id desc limit 1"

# After 30s: cross-source query finds it
/Users/4c/.flyn/memory-router/.venv/bin/flyn-mem query "remediation-test-2026"
```

If all four return as expected, the D1 rubric goes 54/54 and PR #37
can be merged.

---

## Issue B — Openclaw memory-core plugin can't embed locally

### Symptom

When Flyn (via Telegram) is asked to "remember X" or "search what
you know about X", it uses openclaw's built-in `memory-core` plugin
which depends on `node-llama-cpp` for local embeddings. The package
is missing; memory-core falls back to a remote embedding provider
and gets rate-limited:

```
WARN: memory sync failed (search): Error: Local embeddings unavailable.
  Reason: optional dependency node-llama-cpp is missing
WARN: memory embeddings rate limited; retrying in 590ms
WARN: memory embeddings rate limited; retrying in 1199ms
...
```

This is unrelated to our memory-router on :8400. memory-core is
openclaw's own memory layer; memory-router is the orthogonal
flyn-agent memory layer (HOT/WARM/COOL/COLD/LESSON/CONV).

### Root cause

`node-llama-cpp` is an optional native dependency of memory-core
requiring cmake + native build. Either it was never installed
during initial openclaw setup, was installed against an older Node
runtime and broke after a node upgrade, or the build was skipped
on Apple Silicon.

### Three fix options

**B1: Rebuild node-llama-cpp.** Risky on Apple Silicon — cmake
errors are common, may need brew install cmake first, may pick up
wrong Node version. ~15 minutes when it works.

```bash
brew install cmake  # if needed
cd /opt/homebrew/lib/node_modules/openclaw
npm rebuild node-llama-cpp --build-from-source
```

**B2 (recommended): Reconfigure memory-core to use Ollama for
embeddings.** We already have Ollama running on :11434.
Use a small embedding model like `nomic-embed-text` or
`all-minilm`. Pure config change.

```bash
ollama pull nomic-embed-text
# Then edit ~/.openclaw/openclaw.json so memory.embeddings or
# memory-core.embeddings.provider points at the ollama provider.
# The exact key changed across openclaw versions; need to look
# at the current memory-core plugin manifest to confirm.
```

**B3: Replace memory-core with calls to flyn-memory-router.**
Highest effort (write an openclaw plugin that adapts our :8400
API into the memory-core contract). Highest long-term payoff
(unifies all memory access on our hardened code). Not for today.

### Recommendation

**B2.** Cheapest, lowest risk, restores Flyn's natural-language
memory workflow ("remember the secret garden code is...") without
native build dependencies. If Ollama embeddings prove flaky for
quality reasons, escalate to B1 or B3.

### Verification

After B2, send Flyn a unique fact via Telegram:

```
remember that my favorite tea is genmaicha
```

Wait 10 seconds, then in a fresh Telegram message:

```
what do you know about my tea preferences?
```

If Flyn answers "genmaicha", memory-core embedding is restored.
Failing that, check the gateway log for `memory embeddings rate
limited` — if those warnings are gone, the embedder works but
the memory might just not have been indexed yet.

---

## Recommended order

1. **Fix A** (~5 min). Closes the D1 ship-gate, unlocks merging
   PR #37, satisfies the user's existing rubric goal.

2. **Then decide on B.** Independent of A. Issue A gives Flyn-the-bot
   durable per-message Telegram storage in the conv tier; Issue B
   gives Flyn-the-agent-process working in-conversation memory tools.
   They solve different problems and could be done in either order
   or skipped without breaking each other.

## Do NOT

- Do not redo the F1–F5 fixes — those are correct and orthogonal.
- Do not run `install.sh` again — everything it placed is correct;
  the only gap is the openclaw.json hook registration.
- Do not merge PR #37 until a real Telegram message lands in
  ryan.db with a summary AND shows up via `flyn-mem query`. The
  conv tier has been verified by unit + integration tests but never
  by a live end-to-end message round-trip.

## Sources

- Stability sweep + log analysis: gateway log, /tmp/openclaw daily
  log, conv directory state, openclaw channels status output.
- PR #37: `feat/conv-memory-telegram-slice-1`, 18 commits ahead of
  main.
- F1–F5 fixes commit: `44e0773 fix(memory-router): conv-tier
  hardening — 4 stability fixes from review`
- F5 digest fix commit: `3325675 fix(pulses): overnight digest send
  as plain text, not Markdown v1`
