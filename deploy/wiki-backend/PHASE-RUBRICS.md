# OL Wiki Build — Phase Rubrics

Each phase has testable success criteria. A phase is **done** when all its
checks pass. Tests live in `tests/` (pytest) or are listed as manual `# verify:`
commands.

Status legend: ✅ done · 🟡 in progress · ⬜ not started

---

## Phase 0 — Foundation ✅

| # | Criterion | Test |
|---|---|---|
| 0.1 | Flyn Telegram bot live | `openclaw health \| grep -q "Telegram: ok"` ✅ |
| 0.2 | Ryan's chat_id documented | grep workspace/USER.md for `7191564227` ✅ |
| 0.3 | Beth's pairing approved | `openclaw channels list \| grep -q "configured"` ✅ |
| 0.4 | Ollama + gemma4:e4b running | `curl -sS http://127.0.0.1:11434/api/tags \| jq -e '.models[]'` ✅ |
| 0.5 | Neo4j container running | `docker ps \| grep -q flyn-neo4j` ✅ |
| 0.6 | Graphiti REST responding | `curl -sS http://localhost:8100/api/health \| jq -e '.status == "ok"'` ✅ |
| 0.7 | openclaw gateway green | `openclaw health \| grep -q "Agents: main"` ✅ |
| 0.8 | PM skill installed live | `test -f ~/.openclaw/projects/openliteracy/config.yaml && test -d ~/.openclaw/scripts/flyn/pm/` ✅ |
| 0.9 | Wiki auto-deploy live | `launchctl list \| grep -q ai.flyn.ol-wiki-autodeploy` ✅ |

## Phase 1 — Wiki backend ✅

| # | Criterion | Test |
|---|---|---|
| 1.1 | FastAPI app starts + health green | `curl -sS http://127.0.0.1:8200/api/health \| jq -e '.status == "ok"'` ✅ |
| 1.2 | SQLite schema created | `sqlite3 ~/.openclaw/data/ol-pm.db ".tables" \| grep -q "questions"` ✅ |
| 1.3 | 124 questions seeded | `curl -sS http://127.0.0.1:8200/api/stats \| jq -e '.questions_total == 124'` ✅ |
| 1.4 | Tailscale Funnel public | `curl -sS https://4cs-mac-mini.tailc7d8af.ts.net/api/health \| jq -e '.status'` ✅ |
| 1.5 | Wiki fetches from API | `curl -sS https://ol-explainer-wiki.pages.dev/app.js \| grep -q "API_BASE"` ✅ |
| 1.6 | Write endpoints require auth | `curl -sS -o /dev/null -w "%{http_code}" -X POST .../decisions -d '{}' \| grep -q "401"` ✅ |
| 1.7 | Test suite passes | `cd deploy/wiki-backend && .venv/bin/pytest tests/ -q` (8 passing) ✅ |
| 1.8 | Backend survives reboot | launchd `ai.flyn.ol-wiki-backend` has `KeepAlive=true` ✅ |
| 1.9 | First real decision logged | `curl -sS .../decisions \| jq -e '. \| length > 0'` ✅ |

## Phase 2 — Webhooks (Flyn-on-mutation) ✅

| # | Criterion | Test |
|---|---|---|
| 2.1 ✅ | Subscriptions table exists | `sqlite3 ~/.openclaw/data/ol-pm.db ".schema webhooks"` |
| 2.2 ✅ | POST /api/webhooks creates subscription | TestClient: 201 + DB row |
| 2.3 ✅ | GET /api/webhooks lists subscriptions (auth) | TestClient: 200 + list |
| 2.4 ✅ | DELETE /api/webhooks/{id} works (auth) | TestClient: 204 + DB row removed |
| 2.5 ✅ | question.answered fires webhook | TestClient mock receiver: receives POST |
| 2.6 ✅ | decision.created fires webhook | TestClient mock receiver: receives POST |
| 2.7 ✅ | question.reassigned fires webhook | TestClient mock receiver: receives POST |
| 2.8 ✅ | Webhook delivery is best-effort (failures don't break mutation) | TestClient: mutation succeeds even if receiver 500s |
| 2.9 ✅ | Webhook payload includes signature for verification | hmac-sha256 over body with shared secret |
| 2.10 ✅ | Telegram bridge: Flyn DM Beth on decision.created | webhook → Flyn handler → telegram_send to Beth's chat_id |

## Phase 4 — Wiki PM UX ⬜

| # | Criterion | Test |
|---|---|---|
| 4.1 | Gantt-chart view exists in wiki | DOM has `#gantt-section` after load |
| 4.2 | Gantt shows 3 sprint bands with date ranges | Visual via Playwright screenshot match |
| 4.3 | Gantt shows per-stakeholder lanes with question dots | Each owner has a horizontal lane, dots clickable |
| 4.4 | Dependency graph view exists | DOM has `#deps-section` after load |
| 4.5 | Dependency graph renders nodes (questions) + edges (depends_on) | Force-directed via vanilla SVG; deps from questions.depends_on |
| 4.6 | Click question → modal with full detail | Modal shows id, text, ask, source, owner, sprint, deps |
| 4.7 | Modal has "Mark answered" button (auth-gated) | Button visible; click → API call → success/error toast |
| 4.8 | Modal has "Reassign owner" button (auth-gated) | Button + owner dropdown; click → API call |
| 4.9 | Modal has "Log decision" button | Decision form (decided_by, summary, body_md, question_ids) |
| 4.10 | Decision history view | List all decisions with collapsible body_md |
| 4.11 | Per-stakeholder filter button leads to a "your work" view | URL hash routing; shows questions owned by clicked person |
| 4.12 | Auth: PIN gate stays; mutation buttons require API key entry | Modal asks for API key on first mutation; stored in sessionStorage |

## Phase 5 — Outcomes driver ⬜

| # | Criterion | Test |
|---|---|---|
| 5.1 | Python service in `deploy/outcomes/` with venv | `test -d deploy/outcomes/.venv` |
| 5.2 | Accepts rubric in YAML/MD format | unit test: parses rubric file → criteria list |
| 5.3 | Invokes Claude via Anthropic SDK | mock the SDK; verify call shape |
| 5.4 | Grader agent reviews + scores per-criterion | mock; verify multi-iteration loop |
| 5.5 | Max iteration cap (default 5, max 20) | iteration cap enforced |
| 5.6 | Writes per-run report to logs/ | post-run inspection |
| 5.7 | Tooling can target a phase rubric | CLI: `outcomes-runner --rubric PHASE-RUBRICS.md --phase 2` |

## Cross-cutting

| # | Criterion | Test |
|---|---|---|
| X.1 | All write endpoints audit-log the mutation | per-test grep audit_log |
| X.2 | No secrets in versioned files | `git grep -E "(8842152875:AA\|ol_wiki_api token)" -- '*' ':!*.bak'` returns empty |
| X.3 | All Python services have requirements.txt + venv | files exist |
| X.4 | launchd plists at deploy/launchd/ + symlinked to ~/Library/LaunchAgents/ | check both locations |
| X.5 | All commits Co-Authored-By Claude 4.7 1M | `git log --grep "Co-Authored-By: Claude Opus 4.7"` shows recent |

---

## How to drive this rubric

**Manual TDD** (current default): pick the next ⬜ phase. For each criterion,
write the test first, then the implementation. Mark ✅ when test passes.

**Outcomes-driven** (Phase 5+ enables this): point the outcomes-runner at
this file with a phase filter, it iterates Claude on the unmet criteria
until tests pass.

**Reporting**: a `tests/test_phase_status.py` test will scan this file +
the codebase and assert every ✅ has a passing test. Adding new phases
extends the rubric — they start ⬜ and the test allows them.
