# Disaster Recovery — Flyn / OpenClaw on 4C

> **Audience.** Ryan, future Claude, or anyone restoring the 4C deploy from
> scratch after hardware failure, accidental wipe, or migration to a new
> Mac mini.
>
> **Time to recover (target):** Flyn fully back on Telegram + Graphiti +
> wiki backend + Cloudflare wiki within **~90 min** of getting a working
> Mac with internet.

---

## Prerequisites on the new machine

1. macOS recent (tested on 14+, Apple Silicon)
2. Homebrew installed (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`)
3. tmux, node 22 (tarball, NOT brew), Docker Desktop, Ollama 0.21+ installed
4. OpenClaw 2026.4.15+ installed
5. SSH access (for git push from this machine)

## Step-by-step

### 1. Clone the deploy repo

```
mkdir -p ~/AI && cd ~/AI
git clone git@github.com:RShuken/flyn-agent.git
git clone git@github.com:eschnei/OL_LearningPathways_Knowledgebase.git openlit/OL_LearningPathways_Knowledgebase
```

### 2. Restore secrets

Auth profiles aren't versioned. Restore from your password manager / encrypted backup:

```
~/.openclaw/agents/main/agent/auth-profiles.json
```

Expected profiles:
- `openai-codex:ryanshuken@gmail.com` (OAuth, primary)
- `anthropic:default` (OAuth subscription)
- `gemini:default` + `google:default` (same API key in both — Gemini embedder needs both)
- `ollama:default` (token: "local")
- `neo4j:default` (Neo4j password — keep this; Neo4j data won't open with a different one)
- `ol_wiki_api:default` (wiki backend X-API-Key)
- `ol_wiki_bridge:default` (webhook HMAC secret)

If `neo4j:default` is lost: the Graphiti data is lost too (Neo4j auth-encrypted). Bootstrap fresh.

### 3. Restore data from latest backup

Backups are at `~/.openclaw/backups/flyn-state-*.tar.gz` (also pushed off-host via the nightly backup pulse once Drive upload is wired). Restore:

```
mkdir -p ~/.openclaw
cd /tmp && tar -xzf ~/Downloads/flyn-state-latest.tar.gz
mv data ~/.openclaw/
mv workspace/memory/structured/neo4j ~/.openclaw/workspace/memory/structured/
mv projects ~/.openclaw/projects
mv agents/main/sessions ~/.openclaw/agents/main/sessions
```

### 4. Run the installer

```
cd ~/AI/flyn-agent
./deploy/install-flyn.sh
```

This is idempotent. It:
- Validates prereqs (homebrew, node, docker, ollama, openclaw)
- Pulls gemma4:e4b if missing
- Starts Neo4j Docker container (will REUSE existing volumes from step 3)
- Recreates Python venv + installs graphiti-core
- Deploys flyn-graphiti-api.py (with our NodeResolutions patch)
- Deploys workspace files (CONTACTS.md, AGENTS.md, etc.)
- Registers heartbeat crons

### 5. Restore wiki backend

```
cd ~/AI/flyn-agent/deploy/wiki-backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
launchctl load ~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist
```

If the launchd plist isn't there, regenerate it from `deploy/launchd/ai.flyn.ol-wiki-backend.plist` and inject the API key from auth-profiles.json.

### 6. Restore Telegram bridge

```
launchctl load ~/Library/LaunchAgents/ai.flyn.ol-wiki-bridge.plist
```

Same caveat — regenerate from template if missing, inject `OL_BRIDGE_SECRET` env var from auth-profiles.json.

### 7. Restore wiki auto-deploy + Tailscale Funnel

```
cp ~/AI/flyn-agent/deploy/launchd/ai.flyn.ol-wiki-autodeploy.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.flyn.ol-wiki-autodeploy.plist

# Tailscale Funnel
tailscale funnel --bg 8200
```

Verify both with `tailscale funnel status` and `curl http://localhost:8200/api/health`.

### 8. Re-authenticate Cloudflare wrangler

```
wrangler login
# OAuth in browser, then:
wrangler whoami    # confirm
```

(The Pages project + deployment history persist server-side; you just need the local CLI re-auth'd.)

### 9. Verify

| Check | Expected |
|---|---|
| `openclaw health` | Telegram ok, WhatsApp linked, Agents: main |
| `curl http://localhost:8100/api/health` | `{"status": "ok", "neo4j": "connected"}` |
| `curl http://localhost:8200/api/health` | `{"status": "ok", "questions_count": 124+}` |
| `curl https://4cs-mac-mini.tailc7d8af.ts.net/api/health` | same |
| `curl https://ol-explainer-wiki.pages.dev/` | HTTP 200, HTML loads |
| `launchctl list \| grep ai.flyn` | 6+ services running, all exit code 0 |
| `python3 ~/.openclaw/scripts/flyn/pm/morning_standup.py --project openliteracy --dry-run` | digest renders |

### 10. Smoke test end-to-end

Send a test decision:
```bash
API_KEY=$(python3 -c "import json; print(json.load(open('$HOME/.openclaw/agents/main/agent/auth-profiles.json'))['profiles']['ol_wiki_api:default']['token'])")
curl -sS -X POST https://4cs-mac-mini.tailc7d8af.ts.net/api/decisions \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"decided_by":"DR test","summary":"DR verification","body_md":"smoke","question_ids":[]}'
```

Expect: 201 with new decision ID, and a Telegram DM to both Ryan and Beth.

---

## Meeting pipeline (Krisp webhook + nightly categorizer)

**Services in the pipeline:**
- `ai.flyn.ol-wiki-backend` (existing) — hosts `POST /api/meetings/krisp`
- `ai.flyn.pulse.meeting-categorize` (new) — nightly 02:30

**State:**
- `~/.openclaw/data/flyn-meetings.db` — meeting_events, meetings, meeting_audit
- `~/.openclaw/state/last-review-list.json` — morning digest's index→meeting_id map
- `~/.openclaw/openclaw.json` `krisp.webhookToken` — shared secret with Krisp

### Common failures and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `POST /api/meetings/krisp` returns 401 | `FLYN_KRISP_TOKEN` env var not loaded into the wiki-backend launchd job | Edit `~/Library/LaunchAgents/ai.flyn.ol-wiki-backend.plist` `EnvironmentVariables` dict; `launchctl unload && load` |
| Krisp dashboard shows webhook errors | Tailscale Funnel down, or wiki-backend not running | `tailscale funnel status`; `launchctl list \| grep ol-wiki-backend` |
| Categorizer never routes anything | `claude` not on PATH for the launchd context, or no project rules match | Run `bash deploy/cron/scripts/meeting-categorize.sh` by hand and read the log; check `which claude` |
| Meeting stuck in 'classifying' | Categorizer crashed mid-loop | Auto-revert kicks in next run; or run `python3 deploy/pm/meeting_categorizer.py --unstick` |
| Telegram `/route N skip` errors with "no meeting at index N" | Stale state file (older than today's digest) | Re-run morning digest to refresh `~/.openclaw/state/last-review-list.json` |
| Wrong project routing decision | Rules too loose, or LLM hallucinated | Manual `git revert` of the meeting commit; mark DB row `status='dropped'`; tighten rules in project config |

### Disaster: full restore

1. Restore `flyn-meetings.db` from the nightly backup pulse (`~/Backups/flyn/`).
2. `cp deploy/launchd/ai.flyn.pulse.meeting-categorize.plist ~/Library/LaunchAgents/ && launchctl load ...`
3. Re-enter `krisp.webhookToken` into `~/.openclaw/openclaw.json` (rotate by editing Krisp's webhook config to match).
4. Reload wiki-backend.

---

## Common failure modes during DR

| Symptom | Cause | Fix |
|---|---|---|
| Graphiti REST returns 500 on first ingest | Neo4j volume permissions reset | `chmod -R u+rwX ~/.openclaw/workspace/memory/structured/neo4j/` |
| `docker pull neo4j:5.26` "keychain access denied" | macOS keychain locked in SSH session | Run from a GUI Terminal (TeamViewer or local) |
| Telegram bot returns 401 | Old token in openclaw.json after BotFather revoke | `openclaw config set channels.telegram.botToken <new>` + restart gateway |
| Wiki at pages.dev shows old content | Cloudflare cache | `wrangler pages deploy explainer --project-name=ol-explainer-wiki --commit-dirty=true` |
| Cloudflare auth fails | OAuth expired | `wrangler login` |
| MCP tools missing in Claude Code | Need to restart CC after `claude mcp add` | Restart CC; tool enumeration happens at session start |

## What you can lose without affecting recovery

- `/tmp/*.log` files — regenerated as services run
- `~/.openclaw/logs/*` — historical only
- Local git clones — re-clone from GitHub

## What you CANNOT lose

- `~/.openclaw/agents/main/agent/auth-profiles.json` — re-creating means renewing every API/OAuth from scratch (~30 min)
- `~/.openclaw/workspace/memory/structured/neo4j/data/` — Graphiti episodes + facts (loses days of ingestion if rebuilt)
- `~/.openclaw/data/ol-pm.db` — wiki state: questions, decisions, audit, webhooks
- Cloudflare account credentials (loss = wiki goes down + rebuild ~15 min)

These are what the nightly backup pulse targets. Restore from the most recent tarball.

---

## Last DR test

**Not yet performed.** Write the date + outcome here after a successful dry-run.

```
DATE:    [yyyy-mm-dd]
OUTCOME: [pass/fail/partial]
NOTES:   ...
```
