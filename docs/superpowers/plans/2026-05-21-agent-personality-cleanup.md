# Agent Personality Layer Cleanup — Plan

**Date:** 2026-05-21
**Branch:** `chore/agent-personality-cleanup`
**Goal:** Reduce Flyn's system-prompt context from ~50,000 chars to ~2,500 chars
without losing any actual capability. Move what was previously inline prose
into a skills-index pattern where the agent loads a skill body only when its
trigger matches.

## Why

Postmortem (this morning): Flyn responded to "this link is invalid" with a
random memory dump. Root cause was a 20k-char system prompt built up over
8 commits from an imported VC-engagement PM-agent scaffold. The model
faithfully executed memory-routing prose that biased it toward "always
recall" instead of "respond naturally."

See: `docs/superpowers/specs/` (existing) and the chat postmortem.

## Principle

> Agent context should describe **what's available** and **how to find it**,
> not **everything the agent knows**. Skill bodies live on disk and load only
> when their triggers match.

This mirrors how Claude Code skills work: a small description in context,
the body loaded on demand. OpenClaw already supports this — we just haven't
been using it.

## Phases

### Phase 1 — Postmortem doc (10 min)

Save the diagnosis as `docs/remediation/2026-05-21-agent-personality-layer-postmortem.md`
so the lesson outlives this session.

### Phase 2 — New lean AGENTS.md (15 min)

Replace `flyn-agent/workspace/AGENTS.md` (10,466 chars) with ~700-1000
chars. Structure:

1. Identity (1 sentence)
2. Voice / behavioral default (2-3 sentences)
3. Hard rules (5 bullets)
4. Skill discovery pointer (1 line: "see skills/INDEX.md")
5. Lazy-load identity hints (Ryan, Beth, Eric — minimal pointers)

Backup the old file to `workspace/_archive/AGENTS.md.pre-cleanup.md` so
nothing is lost. Diff visible in git anyway.

### Phase 3 — Create skills/INDEX.md (20 min)

A markdown table the agent reads every turn (~2-3k chars). Each row:
`| Skill | Trigger | Path | Type |`

Active skills only — about 15-20 rows. Cron-only skills are listed but
marked as `type: cron` so the agent knows they exist without needing to
invoke them.

### Phase 4 — Restructure flyn-agent/workspace/ to lean form (30 min)

Files that get shrunk or moved:

- `AGENTS.md` (10k → 1k) — see Phase 2
- `TOOLS.md` (9.8k) → move content into per-tool descriptions or
  `skills/_reference/tools.md` (lazy-load)
- `PROJECTS.md` (7.5k) → split into `skills/_reference/projects/cora.md`,
  `projects/openliteracy.md` — lazy-load via the agent's "load project
  context" skill
- `CONTACTS.md` (5.5k) → `skills/_reference/contacts/`; agent loads
  individual contact files on demand
- `IDENTITY.md` (3.9k) → trim to ~500 chars; merge with AGENTS.md
  identity section if small enough
- `SOUL.md` (2.8k) → distill into a single "voice" sentence in AGENTS.md;
  archive the rest
- `USER.md` (3.4k) → `skills/_reference/user.md`; lazy-load
- `HEARTBEAT.md` (3.9k) → `skills/_reference/heartbeat-schedule.md`;
  cron skills don't need it in agent context
- `MEMORY.md` (1.9k) → keep as-is (hot pins; small enough)
- `BOOTSTRAP.md` — already missing; ignore

Target after cleanup: AGENTS.md + MEMORY.md + skills/INDEX.md = ~5k total
loaded every turn. Down from ~50k. **10× reduction.**

### Phase 5 — Add triggers frontmatter to existing skills (40 min)

Many skills already exist under `skills/`. Add a YAML frontmatter to each
that's listed in INDEX.md:

```yaml
---
name: memory-recall
triggers:
  - "what do we know about X"
  - "have we discussed Y"
when-not-to-use:
  - User is reporting an issue (use respond-direct flow)
---
```

Skip the `_archive/`, `_enterprise-v2-reference/`, and `_authoring/`
directories — those are reference only.

### Phase 6 — Audit plugin systemPrompt contributions (15 min)

`active-memory` and `lossless-claw` plugins inject their own systemPrompt
text. Audit + disable or trim. Check `openclaw.json` for plugin config
to disable the most prescriptive "Recall Policy" paragraphs.

### Phase 7 — Deploy + measure (15 min)

1. Run `bash deploy/memory-router/install.sh` (also copies workspace/ files)
2. Verify new file sizes in `~/.openclaw/workspace/`
3. Send a Telegram message; capture the resulting system prompt size from
   the trajectory file
4. Confirm: prompt size drops from ~20k to ~3-5k
5. Confirm: Flyn responds naturally to a normal question

### Phase 8 — Commit + open PR (10 min)

Single commit if the diff is clean; otherwise a logical split. PR title:
`chore(agent): personality layer cleanup — lazy-load skills via INDEX`.

## Out of scope

- Don't touch conv-tier 2.0 (PR #38) or any data-layer code
- Don't delete anything; archive instead — we can prune `_archive/` and
  `_enterprise-v2-reference/` in a follow-up PR once the new structure is
  proven
- Don't rewrite any existing skill bodies; just add frontmatter
- Don't change openclaw or any plugin code; only configuration

## Success criteria

- `~/.openclaw/workspace/AGENTS.md` < 1500 chars
- Total workspace bootstrap injection < 6000 chars
- One real Telegram message: Flyn responds naturally (no memory dump)
- All 5 hard rules still preserved
- Existing skills callable when their triggers match
- No regression in conv-tier-2.0 or memory-router (full test suite passes)

## Rollback

Backups go to `workspace/_archive/<file>.pre-cleanup.md`. If anything
breaks, restore them, re-run install.sh, you're back to where you were.
Branch isolation means rollback is a `git checkout main` away.
