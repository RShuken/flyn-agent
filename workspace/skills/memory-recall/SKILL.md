---
name: memory-recall
triggers:
  - "what do we know about"
  - "have we discussed"
  - "do we have anything on"
  - "what did we decide about"
  - "who is"
  - "remind me about"
when-not-to-use:
  - User is reporting an issue ("X is broken", "Y is invalid", "Z didn't work") → respond to the issue directly; don't dump memory
  - User is making a statement, not asking
  - Same-turn recall (you can just look back at the conversation)
---

# memory-recall

When Ryan is asking what we already know about something.

## Steps

1. **Cross-source query first** (covers HOT/WARM/COOL/COLD/LESSON/CONV tiers):
   ```
   flyn-mem query "<entity or topic>"
   ```
2. **Typed/temporal questions** ("who attended X meeting on Y date") → Graphiti:
   ```
   curl -sS "http://localhost:8100/api/search?q=<entity>"
   curl -sS "http://localhost:8100/api/temporal?q=<entity>&from=YYYY-MM-DD&to=YYYY-MM-DD"
   ```
3. **Reply with the synthesized answer** — not the raw search output.
   Don't dump everything. Pick the 1-3 most relevant facts and answer the
   actual question.

## Anti-pattern

Don't do this:
> "Random pull from the memory stack, grounded from local recall layers:
> 1. Hot-tier memory: Phase 0 ship gate is pinned as..."

That's noise. The user asked a specific question; give a specific answer.

## When the user is reporting an issue

If the phrasing is **"X is broken"** or **"Y is invalid"** → that's a
report, not a question. Switch to `skills/broken-link-fix/SKILL.md` or
just respond directly. Memory recall is for "what do we know" not "X
is broken".
