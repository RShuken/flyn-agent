# Postmortem — Agent Personality Layer Bloat

**Date:** 2026-05-21
**Status:** Diagnosed; remediation plan in `docs/superpowers/plans/2026-05-21-agent-personality-cleanup.md`

## Symptom

Ryan reported a broken link to Flyn on Telegram:
> "this link is invalid: Phase 1 branch: https://github.com/RShuken/flyn-agent/tree/feat/orchestrator-foundation-phase-1"

Flyn responded with a "Random pull from the memory stack" dump:
- 7 sequential memory-lookup bash commands (sed MEMORY.md, flyn-mem queries
  for Cora/Beth/OpenLiteracy/Railway, graphiti search)
- Reply text framed as "Random pull from the memory stack, grounded from
  local recall layers"
- Never addressed the broken link

Total agent latency: ~50 seconds.

## Diagnosis

The model received a **20,001-character system prompt** every turn, most of it
prescribing memory-routing behavior. Sources:

| Source | Approx chars | Contribution |
|---|---|---|
| `AGENTS.md` | ~10,500 | Boot sequence + memory routing + approval gates + auth roles |
| `IDENTITY.md` | ~1,500 | Identity |
| `SOUL.md` | ~800 | Personality voice |
| `USER.md` + `CONTACTS.md` | ~1,200 | People profiles |
| `TOOLS.md` | ~1,800 | Tool descriptions (duplicating the tool registry) |
| `MEMORY.md` | ~1,900 | Hot-tier pinned facts |
| `active-memory` plugin | ~500 | "Memory recall policy" |
| `lossless-claw` plugin | ~1,800 | "Lossless Recall Policy" |
| **TOTAL** | **~20,000** | |

The model isn't reading these as information; it's being **biased** by them.
The center of gravity is "use memory tools first" → so the model interprets
ambiguous input (a complaint about a link) as a memory-lookup opportunity.

## Provenance — where did this come from

`git log -- workspace/AGENTS.md` shows the file grew through 8 commits over
25 days (2026-04-20 → 2026-05-15). All edits were by Ryan (rshuken / 4c
local account); no external imports during that window.

**The seed was your own prior work.** Commit `8b3e975` (2026-04-20,
"Initial source-of-truth drop for OpenClaw deployments") imported
`skills/_enterprise-v2-reference/` — described in the commit as a
"sanitized v2 snapshot from a prior VC engagement (all identifiers
replaced with {{PLACEHOLDER}} tokens)". That reference folder contains a
full PM-agent product:

- `deploy-daily-briefing-v2.md`
- `deploy-action-items.md`
- `deploy-urgent-email-v2.md`
- `deploy-security-council-v2.md`
- `deploy-notion-workspace.md`
- `deploy-personal-crm-v2.md`
- `deploy-knowledge-graph.md`
- `deploy-himalaya-email.md`
- `deploy-testing-suite.md`
- `deploy-voice-cloning-phone-calls.md`
- `deploy-voice-tone-engine.md`

**Direct answer to "Did we try to build a product manager through context?":**
yes. The enterprise PM-agent scaffold was carried over as the seed for
Flyn's personality layer.

**Direct answer to "Did we get this from ClaudeHub?":** no. There's a
`skills/clawhub-recommendations/` folder showing 18 ClawHub picks were
researched but explicitly **not** used. The actual source was your prior
work.

## Why the original template's own guidance was ignored

`templates/AGENTS.md` (the source-of-truth template) opens with:

> "Target: under 200 lines. If this grows large, split operational detail
>  into TOOLS.md and keep AGENTS.md focused on rules + boot sequence."

The template was right. The deploy + accretion violated it. Trajectory:

| Date | Commit | Net lines added | Note |
|---|---|---|---|
| 2026-04-20 | `55c7c2f` | +72 (first deploy) | Initial 4258 chars, still close to template |
| 2026-04-20 | `773d01a` | ±5 | "Recast Flyn as CEO of 4C" — added orchestrator framing |
| 2026-04-20 | `a39b17f` | ±6 | "Strip Rel + OAC references" — cleanup |
| **2026-04-21** | **`c157cd9`** | **+51 (~3000 chars)** | **"Post-mortem cleanup" — paradoxically the BIGGEST growth event** |
| 2026-05-11 | `4aecafd` | +1 | "deploy-project-pm skill + multi-project PM scaffold" |
| 2026-05-12 | `b78c5bd` | +1 | Beth contact policy |
| 2026-05-15 | `2aa238e` | +1 | Memory routing rules + curl examples |
| 2026-05-15 | `965dd45` | +5 | Tool-process rule + 3-tier authorization |

The biggest jump was a commit named "post-mortem cleanup". Intent was
right; execution accreted instead of subtracted.

## Root cause

A skill-driven agent was deployed with the skills inlined into the system
prompt instead of stored on disk and discovered by index. Every new
feature added "just one more directive" to the system prompt instead of
"a new skill the agent can look up when relevant".

## Lessons

1. **System prompts bias, they don't inform.** The shorter the prompt,
   the more the model is free to respond naturally.

2. **Templates with self-imposed constraints work — until deploy reframes them.**
   The "under 200 lines" rule in the template was the right guidance.
   It was lost when the deploy added enterprise PM scaffolding.

3. **"Cleanup" commits need to subtract, not refactor-and-add.** The
   April 21 "post-mortem cleanup" added 51 lines and made the file
   bigger, not smaller. The name lied.

4. **Skills systems already exist (Claude Code, OpenClaw).** When you
   write skills into a system prompt instead of into the skills directory,
   you've reimplemented the worst-performing version of the skills system
   the platform already had.

5. **Importing finished products as seeds is dangerous.** The "sanitized
   v2 snapshot" was complete enough that it shaped the architecture even
   after you intended to recast it. Better to start empty and grow.

## What we're doing about it

See `docs/superpowers/plans/2026-05-21-agent-personality-cleanup.md`. In
brief: replace the 50k-char workspace bootstrap with a ~5k version where
the agent loads skill bodies on demand instead of pre-loading everything.

## What's NOT to blame

- **conv-tier 2.0 / memory-router / encryption.** That's data infrastructure.
  It's sound. It works. Don't conflate "the personality layer was wrong" with
  "everything was wrong".
- **openclaw / codex.** The harness + model are doing what they're told.
  The prompt is what's misdirecting them.
- **The skills directory itself.** It exists; we just weren't using it
  correctly.
