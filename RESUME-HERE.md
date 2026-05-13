# RESUME HERE — Flyn OpenLit Build

> Drop this prompt into any new Claude Code session opened in `/Users/4c/AI`:
>
> **"Read `/Users/4c/AI/flyn-agent/RESUME-HERE.md`, then check the live state with the commands inside. Continue from there."**
>
> You'll have full context in under 60 seconds.

**Last session ended:** 2026-05-13
**Scope:** Flyn as PM agent — **OpenLiteracy ONLY** for now. Cora is on the roadmap but not actively tracked.
**Operator:** Ryan Shuken (Telegram chat_id `7191564227`)
**Partner:** Beth Kukla, co-founder + COO Cora; PM for OL (chat_id `7434192034`)
**Tech lead:** Eric Schneider — pending Telegram (`@flyn_4c_bot` /start required)

---

## What to read first (in order)

1. **This file** — overview + live-state checks
2. `/Users/4c/AI/flyn-agent/deploy/outcomes/SESSION-REPORT-2026-05-12.md` — comprehensive recap of the whole build
3. `/Users/4c/AI/flyn-agent/deploy/outcomes/READINESS-RUBRIC.md` — 10-dimension Flyn-as-PM eval (current score: 3.4/5)
4. `/Users/4c/AI/flyn-agent/workspace/WIKI.md` — OL wiki API reference; **Flyn loads this every turn**
5. `/Users/4c/AI/flyn-agent/workspace/CONTACTS.md` — Beth + Eric trust policies
6. `/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase/docs/00-source/meetings/2026-05-11_sprint1-kickoff/synthesis.md` — verbatim Sarah/Rebecca/Greta quotes + 10 design principles

---

## Live state — verify everything is up

```bash
# 1. All 4C services should be running
launchctl list | grep -E "ai\.(flyn|openclaw)" | head -15

# 2. Wiki API
curl -sS http://127.0.0.1:8200/api/stats | python3 -m json.tool

# 3. Wiki public URL (PIN 1080)
curl -sS -o /dev/null -w "wiki public: HTTP %{http_code}\n" https://ol-explainer-wiki.pages.dev/

# 4. Tailscale Funnel
curl -sS -o /dev/null -w "API public: HTTP %{http_code}\n" https://4cs-mac-mini.tailc7d8af.ts.net/api/health

# 5. Graphiti
curl -sS http://localhost:8100/api/health

# 6. openclaw gateway + Flyn
openclaw health | head -10

# 7. Telegram bot identity
curl -sS "https://api.telegram.org/bot$(python3 -c 'import json; print(json.load(open("/Users/4c/.openclaw/openclaw.json"))["channels"]["telegram"]["botToken"])')/getMe" | python3 -m json.tool

# 8. Linear sync state
sqlite3 ~/.openclaw/data/ol-pm.db "SELECT COUNT(*) AS synced, (SELECT COUNT(*) FROM questions) AS total FROM questions WHERE linear_issue_id IS NOT NULL"
```

Expected results: all green, **~73 of 124** Linear issues synced (see "Known issues" below).

---

## Live surfaces

| What | Where |
|---|---|
| Wiki (public, PIN 1080) | https://ol-explainer-wiki.pages.dev |
| Wiki API | https://4cs-mac-mini.tailc7d8af.ts.net/api · http://127.0.0.1:8200/api (local) |
| Linear project | https://linear.app/rshuken/project/openliteracy-phase-2-320bbd515474 |
| MCP server | `ol-wiki` registered in Claude Code (`claude mcp list` → ✓ Connected, 8 tools) |
| Flyn-on-4C | Telegram `@flyn_4c_bot`, openclaw gateway, workspace at `~/.openclaw/workspace/` |
| Source repos | `/Users/4c/AI/flyn-agent` (private; this repo) · `/Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase` (private) |

---

## Recent commits (latest 5)

```bash
git log --oneline -5
cd /Users/4c/AI/openlit/OL_LearningPathways_Knowledgebase && git log --oneline -5
```

Should show recent dates and `Co-Authored-By: Claude Opus 4.7`.

---

## In-flight / next actions (priority order)

### 1. Linear free-tier issue cap blocks the last 51 questions

**Status:** 73 of 124 wiki questions synced to Linear. The remaining 51 hit `USAGE_LIMIT_EXCEEDED` — Ryan's Linear workspace already had ~200 RSH issues, the OL sync pushed it over the free-tier active-issue cap.

**Resolution paths:**
- Pay for Linear Starter (~$10/mo per user) — unlocks unlimited issues, then re-run `linear_sync.py`
- Close old RSH issues to free slots, then re-run
- Accept partial sync; the 51 unsynced are mostly section L (AI gen), M (schema), N (conflicts), P (group cadence) — Ryan's call

**To resume sync after upgrading or freeing slots:**
```bash
cd /Users/4c/AI/flyn-agent/deploy/wiki-backend
.venv/bin/python linear_sync.py    # idempotent; only creates issues that don't have linear_issue_id yet
```

### 2. Pearl Platform video transcription

Rebecca's "Lesson Sharing within Pearl Platform.mp4" (27MB) explains the co-browser issue from a different angle than her 5/11 quote. Drive MCP session went stale; needs manual download.

```bash
# Once file is at /tmp/lesson-sharing.mp4:
/Users/4c/AI/flyn-agent/deploy/pulses/video_transcribe.sh \
  /tmp/lesson-sharing.mp4 openliteracy 2026-05-11_pearl-platform-video
```

Then update question **I.13** with the concrete findings.

### 3. Eric Telegram onboarding

Eric Schneider (tech lead) needs to find `@flyn_4c_bot` in Telegram and tap Start. Once he does:

```bash
TOKEN=$(python3 -c 'import json; print(json.load(open("/Users/4c/.openclaw/openclaw.json"))["channels"]["telegram"]["botToken"])')
curl -sS "https://api.telegram.org/bot${TOKEN}/getUpdates" | python3 -m json.tool | grep -B 2 -A 5 -i "eric\|schneider"
# Extract Eric's chat_id, then update workspace/CONTACTS.md
```

### 4. Run the first real Outcomes session

The `outcomes_runner.py` now has a `claude -p` backend (no API key needed; uses Ryan's Claude Code subscription on 4C). Try a real grader pass:

```bash
cd /Users/4c/AI/flyn-agent/deploy/outcomes
.venv/bin/python outcomes_runner.py \
  --rubric READINESS-RUBRIC.md \
  --phase 1 \
  --max-iter 3
```

### 5. Graphiti episode bootstrap completion

91/124 OL questions are episodes in Graphiti. 33 remaining failed on **Gemini free-tier embedding quota** (resets daily). Re-run when fresh:

```bash
python3 ~/.openclaw/scripts/flyn/pm/registry_parser.py --project openliteracy --bootstrap
```

Bootstrap is idempotent — only ingests what isn't there yet.

### 6. CI/CD agents (planned, not started)

The readiness rubric Dimension 4 (Code + CI/CD) is at 3/5 because there are no automated PR-review or auto-doc agents yet. Architecture sketched in `READINESS-RUBRIC.md` ("CI/CD agent architecture" section).

---

## Known issues

1. **Linear sync: 73/124** — see action #1 above
2. **Graphiti bootstrap: 91/124** — Gemini quota; retries daily
3. **MCP Drive session expires randomly** — for large/long downloads, fall back to manual download via TeamViewer
4. **Anthropic auth is OAuth not API key** — Outcomes-native API needs API key. `claude -p` works as a substitute (subscription-billed)
5. **`@flyn_4c_bot` token has been in conversation history twice** — Ryan accepted the risk; if security posture changes, rotate via BotFather

---

## What's working great

- Wiki public + API + MCP server: all live, all auto-deployed
- Webhook → Telegram bridge: live (Beth + Ryan get pings on every mutation)
- Question modal with mutation buttons in the wiki: working end-to-end
- Gantt + 10 design principles + decisions log all in the wiki UI
- Morning standup delivers via real Telegram DM
- Nightly backup pulse: daily 02:17 tarball
- DR run-book: `flyn-agent/DISASTER-RECOVERY.md` (untested)
- Readiness rubric at 3.4/5 with concrete top-10 gap list

---

## How to continue (the simple prompt)

In any fresh Claude Code session in `/Users/4c/AI`, paste this single line:

> **"Read `flyn-agent/RESUME-HERE.md`, run the live-state checks inside, and tell me what's the highest-value next action."**

Claude will load this doc, check live state, and recommend the next concrete move with priority and effort estimate. You direct from there.

---

*Saved 2026-05-13 by Claude Opus 4.7 (1M context). Auto-memory pointer at `~/.claude/projects/-Users-4c-AI/memory/project_openliteracy_flyn_build.md`.*
