# OpenClaw Audit Baseline

**Built:** 2026-04-19
**Built by:** Claude (5 parallel research subagents + direct 4C live probes + local file reads)
**Purpose:** The standards document that the skill audit (`skill-audit-2026-04-19.md`) grades against. Every claim has a citation. Future readers can challenge any standard by checking the cited source.

## How to Read This Document

- **§1–§8** = Official platform standards from `docs.openclaw.ai`
- **§A–§H** = ClawHub registry standards (`clawhub.ai`) — what the rest of the OpenClaw ecosystem looks like
- **§I–§O** = Reddit community signals (pain points, recommended patterns, deprecations)
- **§P–§V** = GitHub public repo patterns (how others structure skill libraries)
- **§W–§BB** = Current model IDs (Anthropic + OpenAI/Codex) for the Codex strip pass
- **§4C** = Ground-truth probes from Mac Mini 4C (live 2026.4.15 install)
- **§Local** = Findings from auditing our own `_authoring/` docs against everything above

When a skill is graded, the Evidence column should cite **§ numbers** from this document.

---

## §1 Current OpenClaw version & install method

- **Latest stable:** `2026.4.15` (released April 2026)
  - Source: `https://docs.openclaw.ai/install/index.md`
  - Confirmed live on Mac Mini 4C: `OpenClaw 2026.4.15 (041266a)` — see §4C.1
- **Latest beta:** `2026.4.19-beta.2`
- **Version scheme:** `YYYY.M.D` (no zero padding)
- **Node:** Node 24 recommended; Node 22.14+ minimum (`engines >=22.14.0`)
  - Source: `https://docs.openclaw.ai/install/node.md`
- **Official install commands:**
  - macOS/Linux/WSL2: `curl -fsSL https://openclaw.ai/install.sh | bash`
  - Windows PowerShell: `iwr -useb https://openclaw.ai/install.ps1 | iex`
  - Local-prefix variant (Node + OpenClaw under `~/.openclaw`): `curl -fsSL https://openclaw.ai/install-cli.sh | bash`
  - npm path: `npm install -g openclaw@latest && openclaw onboard --install-daemon` (pnpm/bun also documented)
- **Binary location:** npm global bin (`$(npm prefix -g)/bin/openclaw`); local-prefix variant puts both Node and openclaw under `~/.openclaw`
- **Verification commands:** `openclaw --version`, `openclaw doctor`, `openclaw gateway status`
- **Managed startup:** macOS LaunchAgent via `openclaw gateway install`; Linux systemd user service; Windows Scheduled Task

---

## §2 Workspace layout (canonical)

Source: `https://docs.openclaw.ai/concepts/agent-workspace.md`, `https://docs.openclaw.ai/start/openclaw.md`

- **Default workspace:** `~/.openclaw/workspace` (or `~/.openclaw/workspace-<profile>` when `OPENCLAW_PROFILE` set)
- **Override:** `agent.workspace` in `~/.openclaw/openclaw.json`
- **State directory** (separate, NOT inside workspace): `~/.openclaw/` holds:
  - `openclaw.json` (config)
  - `agents/<agentId>/agent/auth-profiles.json` (credentials — NOT in openclaw.json)
  - `agents/<agentId>/sessions/`
  - `cron/jobs.json`
  - `skills/` (user-installed skills)
  - `credentials/`
- **Workspace bootstrap files** (auto-created by `openclaw onboard|configure|setup`):
  - Required-ish: `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `TOOLS.md`, `HEARTBEAT.md`
  - Optional: `BOOT.md`, `BOOTSTRAP.md` (one-time only), `MEMORY.md`, `memory/YYYY-MM-DD.md`, `skills/`, `canvas/`
- **Live confirmation on 4C** (§4C.2): all of AGENTS/BOOTSTRAP/HEARTBEAT/IDENTITY/MEMORY/SOUL/TOOLS/USER.md are present at `~/.openclaw/workspace/`

---

## §3 Cron / scheduling API (current as of 2026.4.15)

Source: `https://docs.openclaw.ai/automation/cron-jobs.md`, `https://docs.openclaw.ai/cli/cron.md`, live probe §4C.3

### Subcommands available (live verified on 4C)
`add | disable | edit | enable | list | rm | run | runs | status`

### Flags supported
`--name`, `--at | --every | --cron`, `--tz`, `--session main|isolated|current|session:<id>`, `--message`, `--model`, `--thinking`, `--tools`, `--light-context`, `--wake now|next-heartbeat`, `--announce --channel <ch> --to <target>`, `--webhook`, `--no-deliver`, `--exact`, `--stagger`, `--delete-after-run`, `--agent <id> | --clear-agent`

### Storage
`~/.openclaw/cron/jobs.json` (configurable via `cron.store`)

### Engine
Croner (5- or 6-field expressions). Watch out for the Vixie-cron OR-semantics gotcha on DoM/DoW.

### Webhook triggers
`POST /hooks/wake`, `POST /hooks/agent`, `POST /hooks/<name>` when `hooks.enabled: true`. Bearer-token or `x-openclaw-token` only — query-string tokens rejected.

### DEPRECATED in cron API
- **`--deliver`** is a deprecated alias for `--announce` (https://docs.openclaw.ai/cli/cron.md)
- **`cron.webhook`** global fallback URL: "deprecated legacy fallback" used only for jobs with `notify: true`
- **Legacy field normalizations** (handled by `openclaw doctor --fix`): `jobId`, `schedule.cron`, top-level delivery fields, legacy `threadId`, payload `provider` aliases

### Critical anti-patterns from the community (§K, §L)
- **Don't fan out N cron jobs on the same schedule** — collapse into one heartbeat turn (5x cost savings)
- **Use `--session isolated`** for cron to avoid polluting main chat context
- **Use `--exact` or `--stagger 30s`** when timing matters

---

## §4 Model configuration schema

Source: `https://docs.openclaw.ai/concepts/model-providers.md`, `https://docs.openclaw.ai/concepts/model-failover.md`

### Config file
`~/.openclaw/openclaw.json` (JSON5 — comments allowed)

### Model reference format
`provider/model` — e.g., `opencode/claude-opus-4-6`, `anthropic/claude-opus-4-6`, `openai/gpt-5.4-mini`, `openai-codex/gpt-5.3-codex`

### Key paths in openclaw.json
| Path | Purpose |
|------|---------|
| `agents.defaults.model` | Primary model |
| `agents.defaults.model.fallbacks` | Ordered fallback list |
| `agents.defaults.models` | Allowlist (empty = default; non-empty REPLACES, does not merge) |
| `models.providers.<id>.models[].contextWindow` | Provider metadata |
| `models.providers.<id>.models[].contextTokens` | Runtime cap |

### Auth (where it actually lives)
`~/.openclaw/agents/<agentId>/agent/auth-profiles.json` — runtime-only, NOT in openclaw.json. The `auth.profiles` / `auth.order` keys in openclaw.json are metadata/routing only.

### Documented providers (47 total)
Anthropic, OpenAI, **Codex (bundled `codex` provider paired with Codex agent harness)**, Google (Gemini), OpenRouter, Ollama, LM Studio, vLLM, SGLang, LiteLLM, Mistral, DeepSeek, Groq, Fireworks, Together, xAI, Perplexity, Cohere (via aliases), GitHub Copilot, Alibaba, Arcee, AWS Bedrock (+ Bedrock Mantle), Chutes, Claude Max API Proxy, Cloudflare AI Gateway, ComfyUI, Deepgram, fal, GLM (Zhipu), Hugging Face, inferrs, Kilocode, MiniMax, Moonshot, NVIDIA, OpenCode/OpenCode Go, Qianfan, Qwen, Runway, StepFun, Synthetic, Venice, Vercel AI Gateway, Volcengine (Doubao), Vydra, Xiaomi MiMo, Z.AI

### NOT documented
- **"oMLX"** does not appear anywhere in OpenClaw docs. The reference engagement assumes oMLX. This is a portability concern for any skill that hard-requires it.

### CLI surface (live verified on 4C §4C.4)
`openclaw models {aliases | auth | fallbacks | image-fallbacks | list | scan | set | set-image | status}`

---

## §5 Identity / Soul / Heartbeat conventions

Source: `https://docs.openclaw.ai/reference/templates/{IDENTITY,SOUL,HEARTBEAT}.md`, `https://docs.openclaw.ai/concepts/soul.md`, `https://docs.openclaw.ai/gateway/heartbeat.md`

### Files (all live at workspace root, default `~/.openclaw/workspace/`)

| File | What it does | Loaded when |
|------|--------------|-------------|
| `IDENTITY.md` | name, creature, vibe, emoji, avatar (workspace-relative path, http(s) URL, or data URI) | Every turn |
| `SOUL.md` | persona, tone, boundaries, bluntness | Every normal session |
| `HEARTBEAT.md` | optional checklist for periodic heartbeat runs | Every heartbeat (default 30 min) |
| `AGENTS.md` | boot sequence, rules, checklist routing | Every turn |
| `USER.md` | who the user is | Every turn |
| `TOOLS.md` | what tools are available + how to use them | Every turn |
| `BOOTSTRAP.md` | one-time boot ritual | First boot only |
| `BOOT.md` | optional alternate boot doc | If present |
| `MEMORY.md` | long-term memory index | Gated — **NEVER load in group chats or sub-agents** (§Q, §R) |

### Heartbeat behavior
- Default: every 30 minutes
- Disable: `agents.defaults.heartbeat.every: "0m"`
- Empty/comments-only HEARTBEAT.md → heartbeat API calls are skipped
- Agent-reply `HEARTBEAT_OK` suppresses delivery
- **Heartbeat model override is BROKEN** (§M, GitHub #9556) — config silently falls through to primary model

### Community split (§R, win4r/openclaw-workspace)
- "Soul is what the model embodies. Identity is what users see — they don't have to match."
- Minimum viable workspace: AGENTS.md + SOUL.md + TOOLS.md
- MEMORY.md security rule: never loaded in group chats or sub-agents — boot sequence must enforce

---

## §6 Skill anatomy (CRITICAL — our format diverges from canon)

Source: `https://docs.openclaw.ai/tools/skills.md`, `https://docs.openclaw.ai/tools/creating-skills.md`, `https://docs.openclaw.ai/tools/clawhub.md`

### Canonical OpenClaw skill format
- **AgentSkills-compatible directory** containing `SKILL.md`
- `SKILL.md` has YAML frontmatter + markdown body
- **Required frontmatter:** `name` (snake_case unique), `description` (one line)
- **Optional frontmatter:** `homepage`, `user-invocable` (default true), `disable-model-invocation`, `command-dispatch: tool`, `command-tool`, `command-arg-mode: raw`
- **Load-time gating** via `metadata`: `metadata.openclaw.os`, `metadata.openclaw.requires.bins`, `metadata.openclaw.requires.config`

### Load precedence (highest → lowest)
1. `<workspace>/skills`
2. `<workspace>/.agents/skills`
3. `~/.agents/skills`
4. `~/.openclaw/skills` (user-installed via `openclaw skills install`)
5. Bundled skills (43/52 ready on live 4C, §4C.5)
6. `skills.load.extraDirs` (config-driven extras)

### Per-agent allowlists
`agents.defaults.skills` + `agents.list[].skills` — empty array = no skills; non-empty REPLACES (does not merge with) defaults.

### Registry CLI (live confirmed on 4C §4C.4)
- `openclaw skills install <slug>`
- `openclaw skills update --all`
- `openclaw skills list`
- `openclaw skills check` / `info` / `search`
- Separate `clawhub` CLI for publish/sync

### **CRITICAL FINDING — our format is NON-CANONICAL**
Our `deploy-X.md` flat-file naming is idiosyncratic. The OpenClaw canon is:
```
skills/<author>/<skill-slug>/SKILL.md
```
Confirmed in `openclaw/skills` (the official archive of every published version), GitHub `openclaw/openclaw`, and ClawHub URL pattern `clawhub.ai/@<owner>/<slug>`.

**Implication:** every `deploy-X.md` in our base would need conversion before it could be published to ClawHub. Our format works as INTERNAL runbooks (operator-invoked deployment scripts) but NOT as agent-loadable skills.

---

## §7 Documented deprecations (do not use)

| Deprecated | Use instead | Source |
|-----------|-------------|--------|
| `--deliver` (cron) | `--announce` | docs.openclaw.ai/cli/cron.md |
| `cron.webhook` global fallback URL | `webhooks` API | configuration-reference.md L3699,3714 |
| TCP bridge protocol (`bridge.*` config keys) | (removed entirely; `openclaw doctor --fix` strips) | configuration-reference.md L3670 |
| `~/.openclaw/agent/auth-profiles.json` | `~/.openclaw/agents/<agentId>/agent/auth-profiles.json` | model-failover.md |
| `~/.openclaw/sessions/sessions.json` | `~/.openclaw/agents/<agentId>/sessions/sessions.json` | start/openclaw.md |
| `~/.openclaw/credentials/oauth.json` | imported into `auth-profiles.json` on first use | start/openclaw.md |
| `routing.groupChat.mentionPatterns` | `messages.groupChat.mentionPatterns` | index.md vs start/openclaw.md |
| `ssrfPolicy.allowPrivateNetwork` | retained as legacy alias | configuration-reference.md L2932 |
| `mainKey` config field | `main` (runtime always uses `main`) | configuration-reference.md L1997 |
| AGENTS.md `Every Session` / `Safety` headings | `postCompactionSections` | configuration-reference.md L1411 |
| Older `~/openclaw` workspace folder | `~/.openclaw/workspace` (`openclaw doctor` warns) | concepts/agent-workspace.md |

---

## §8 Recent change log highlights (relevant to skill audit)

Source: `https://github.com/openclaw/openclaw/blob/main/CHANGELOG.md`

### 2026.4.18
- **Added Claude Opus 4.7 `xhigh` reasoning effort**
- Control UI settings overhaul + presets + quick-create
- macOS `screen.snapshot` for app nodes (impacts video-analysis skills)
- WhatsApp multi-account isolation
- Codex OAuth bridging fixes (multiple)
- Cron main-session delivery preserves `heartbeat.target="last"`

### 2026.4.15 (current 4C version)
- **Defaulted Anthropic/`opus` aliases/Claude CLI/bundled image understanding to Claude Opus 4.7**
- Added Gemini TTS to bundled `google` plugin
- Control UI Model Auth status card + `models.authStatus` API (60s cache)
- memory-lancedb cloud storage
- GitHub Copilot embedding provider for memory search
- **Experimental** `agents.defaults.experimental.localModelLean` to strip heavy default tools for weaker local models
- Skills-snapshot invalidation fixes
- Ollama prefix-stripping fixes

---

## §A ClawHub registry overview

Source: research subagent — https://clawhub.ai (Convex backend, Vercel-hosted SPA)

- **52,700–57,100 skills** (live count)
- **180,000 users**
- **12,000,000 downloads cumulative**
- **4.8 average rating**
- Public browsing open; **GitHub sign-in required to publish/install**
- Hybrid registry: community-uploaded with VirusTotal + OpenClaw security scans

### Categories
All, MCP Tools, Prompts, Workflows, Dev Tools, Data & APIs, Security, Automation, Other

### Trust signals
- "Staff picks" filter
- "Hide suspicious" toggle (+ `?nonSuspicious=true` query param)
- Per-skill VirusTotal + OpenClaw scan badges (Benign / Suspicious + confidence + rationale)
- No "Anthropic-official" tier
- Credibility = downloads + stars + staff picks

### Naming
- Canonical URL: `clawhub.ai/@<owner-handle>/<skill-slug>`
- Slugs: lowercase kebab-case
- Heavy use of vendor prefixes: `gws-gmail`, `lark-calendar`, `expanso-email-triage`
- **No `deploy-X` prefix observed** — that's our convention

---

## §B Top ClawHub skills overlapping our themes

(Citations are abbreviated — full URLs in research notes)

| Our skill | Closest ClawHub equivalent(s) | Notes |
|-----------|-------------------------------|-------|
| identity / persona / soul | `verified-agent-identity`, `molt-identity`, `soul-md`, `soul-framework`, `persona-crafter` | Our IDENTITY/SOUL split is community-aligned |
| security council | `council-of-the-wise`, `pentest`, `security-reviewer`, `pr-reviewer`, `daily-review-ritual` | No direct "nightly security council" in registry |
| daily briefing | `morning-daily-briefing`, `ai-daily-briefing`, `daily-news-briefing` | Strong match |
| urgent email | `email-triage`, `expanso-email-triage`, `email-triage-pro` | Strong match |
| meeting prep | `meeting-prep`, `ai-meeting-prep`, `meeting-prep-agent` | Strong match |
| transcript pipeline | `fathom-api`, `fathom-meetings`, `video-transcript-downloader` | Match for source step |
| knowledge base | `private-knowledge-base`, `rag-search`, `hk101-living-rag` | Strong match |
| personal CRM | `personal-crm`, `heleni-personal-crm`, `crm` | Strong match |
| notion | `@steipete/notion`, `notion-skill`, `notion-cli`, `notion-api-skill` (managed-OAuth) | Steipete is most popular |
| linear | `linear-api`, `linear-skill`, `linear-autopilot` | Match |
| github CI/CD | `github-code-review-cicd`, `openclaw-github-assistant`, `@steipete/github` | Strong match |
| google workspace | `@steipete/gog`, `google-workspace-mcp`, `google-workspace-cli`, `gws-gmail`, `gws-gmail-send` | Steipete's `gog` is most popular |
| git autosync | `git-essentials`, `git-workflows` | **No exact "hourly autocommit" equivalent** |
| db backups | `openclaw-backup`, `claw-backup`, `git-crypt-backup` | Match |
| model tracking | `session-cost-tracker`, `expenses` | **No model-cost dashboard equivalent** |
| telegram setup | `agent-telegram`, `telegram-voice-group`, `rho-telegram-alerts` | Builtin `telegram:configure` + `:access` exist (visible in our active skills list) |
| image gen | `nano-banana-pro` (Gemini 3 Pro), `best-image-generation`, `cheapest-image-generation` | Strong match |
| video gen | `video-generation-minimax` | Match |
| humanizer | `@biostartechnology/humanizer`, `ai-humanizer`, `humanizer-enhanced` | Strong match — popular |
| food journal | `food`, `healthy-eating` | **No dedicated food-journal at scale** |
| newsletter / beehiiv | `newsletter-digest`, `newsletter-creation-curation`, `beehiiv-integration`, `beehiiv` (managed-OAuth) | Match |
| asana | `asana-api` (managed-OAuth), `asana-pat`, `asana-agent-skill` | Match |
| earnings | `earnings-reader`, `earnings-calendar`, `stock-earnings-review` | All stock-market focused, NOT personal-revenue |
| tiktok / youtube | `tiktok-uploader`, `publora-tiktok`, `upload-post` (cross-platform), `linkedin-automation` | Match — `upload-post` is the popular cross-platform |

### Implications
- **Many of our skills duplicate community work.** Before keeping a skill, check if a popular ClawHub alternative does it better — and would be lighter to maintain.
- **No equivalents for:** model-cost dashboard, hourly git-autosync, scaled food journal, personal-revenue earnings tracker. These are uniquely our value-adds.

---

## §H Structural patterns from popular ClawHub READMEs

Pattern observed across `self-improving-agent`, `gog`, `notion`, `humanizer`, etc.:

1. **H1 = human title**, then one-paragraph pitch ("Use when…")
2. **First-Use Initialisation** block with idempotent `mkdir -p` and explicit "Never overwrite existing files / Do not log secrets" warnings
3. **Quick Reference table** (Situation → Action) BEFORE long prose — optimized for model retrieval
4. **Platform sections:** "OpenClaw Setup (Recommended)" with `clawhub install <slug>` + manual `git clone` path, then "Generic Setup (Other Agents)" for Claude Code / Codex / Copilot. Explicit mentions of `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `SOUL.md`, `TOOLS.md`, `MEMORY.md`
5. **Formatted log/output templates** with strict ID scheme (`TYPE-YYYYMMDD-XXX`) and frontmatter-style fields (Logged/Priority/Status/Area/Summary/Details/Metadata/Resolution)
6. **Hook integration** section: `cp hooks/openclaw ~/.openclaw/hooks/<slug>` → `openclaw hooks enable <slug>`
7. Prominent **security disclaimer** above the README ("Like a lobster shell, security has layers — review code before you run it")

---

## §I–§O Reddit community signals

⚠️ **Caveat:** Reddit blocked direct WebFetch from the research environment. ~70% of thread metadata (titles, upvotes) comes from secondary aggregators (kilo.ai, managemyclaw.com, simen.ai). The 5 Reddit URLs in §J are verbatim-confirmed.

### §I Subreddits
- **r/openclaw** — exists, ~103k members
- **r/clawdbot** — legacy name, still active (pre-rebrand)
- r/OpenClawAI, r/ClawHub — **not found**
- r/LocalLLaMA, r/selfhosted, r/AutoGPT, r/AI_Agents, r/automation — exist, OpenClaw cross-discussed

### §J Notable threads (URLs confirmed)
- "Biggest unlock was letting agent improve its own environment" — r/clawdbot/comments/1rs7yns
- "Does OpenClaw actually do anything for you guys?" — r/openclaw/comments/1r0wks3
- "I ignored all red flags to give OpenClaw root" — r/openclaw/comments/1rtpk8e
- "What are real everyday use cases for OpenClaw?" — r/openclaw/comments/1rc06ee
- "I spent 4 days setting up OpenClaw, here's the…" — r/openclaw/comments/1rlc7fr

### §K Common pain points (recurring complaints)
1. **Update instability** — "v2026.3.2 shipped 3 simultaneous breaking changes with zero migration guide." 13 point releases in March 2026.
2. **Tools/exec silently disabled after updates** — 2026.3.2 made exec/filesystem tools opt-in by default (GitHub #34810). Users report "my agent went dumb."
3. **Memory compaction = silent hallucinations** — multi-step procedures and structured data flattened into summaries.
4. **Cost blowups** — $1–3/day baseline, reports of $131/day on Opus, some users $5k cumulative.
5. **Setup complexity** — single most-upvoted complaint per kilo.ai analysis.
6. **Local-model tool calls broken via Ollama streaming** — GitHub #5769, #17385, #60601, #32916. Workaround: `OLLAMA_DISABLE_STREAMING=true`.
7. **Heartbeat model override BROKEN** — GitHub #9556. Heartbeat always uses primary (expensive) model despite config.
8. **Skill security** — 341–386 malicious skills found on ClawHub Feb 1–3, 2026 (crypto-exchange API exfil, SSH key theft).
9. **Matrix messaging broken since 2026.2.17** (3+ versions).
10. **"Silent replies" bug** when `thinkingDefault: high` — agent processes but response never delivers.

### §L Community-recommended patterns
- **Cron for exact timing, heartbeat for periodic awareness sweeps.** Batch monitoring checks into one heartbeat (5x cheaper than 5 cron jobs).
- **Heartbeat: cheap-checks-first, model only on alert.** Tiny script outputs `HEARTBEAT_OK` or `HEARTBEAT_ALERT <list>`; LLM invoked only on alert. Pointing heartbeat at local Ollama drops cost from $5–15/mo to ~$0.
- **Identity split:** SOUL.md (philosophy) + IDENTITY.md (name/voice/emoji) + MEMORY.md index.
- **Memory: hierarchical folders + weekly compaction** of MEMORY/ into one summary file every Monday. Past ~400 files = collapse on compaction.
- **Hybrid model routing:** Claude Sonnet 4.5 for user-facing/hard reasoning; local Ollama (gemma-4, glm-4.7-flash, qwen) for heartbeat/cron/embeddings. (Matches user's existing `feedback_openclaw_local_background_routing.md`.)
- **Read-only first.** Start with read-only data sources and narrow channels before write access.
- **Install Clawdex / skill vetter BEFORE adding ClawHub skills** — pre-install + retroactive scanning.

### §M Deprecation reports from community
- Pre-rename ClawdBot config keys → run `openclaw doctor --fix`
- Sandbox model changed in 2026.3.x → exec permissions now explicit
- Tool defaults flipped to opt-in in 2026.3.2 → fresh configs need explicit enablement
- `heartbeat.model` override silently ignored since 2026.2.3-1
- Matrix integration broken since 2026.2.17
- Streaming + Ollama tool calls regressed → `OLLAMA_DISABLE_STREAMING=true`

### §N Model preferences (community)
- **Claude Sonnet 4.5/4.6** = community default for primary OpenClaw sessions
- **Claude outperforms GPT-4o** on long-context, prompt-injection resistance, multi-step tool use
- **Opus 4.7 backlash** — "Claude Opus 4.7 Is a Regression" (roborhythms.com) — worse tool-use for the price
- **Local for cost, not quality** — Gemma 4 (16GB Mac) and glm-4.7-flash named best free locals; Qwen 2.5 Coder 32B has tool-call issues; 7–8B models malform structured tool calls
- **Hybrid is consensus**

### §O Anti-patterns (community warnings)
- Don't give OpenClaw root before testing read-only
- Don't install ClawHub skills without vetting source (341+ malicious skills Feb 2026)
- Don't connect messaging channels without prompt-injection mitigations (email PI extracting private keys demonstrated)
- Don't fan out N cron jobs — collapse into heartbeat
- Don't let MEMORY/ folder grow unbounded past ~400 files
- Don't rely on `heartbeat.model` override — verify with token logs
- Don't deploy local-model-only setups for production (tool-call malformation)
- Don't auto-update mid-workflow (pin versions; test on second enrollment first)

---

## §P–§V GitHub public repo patterns

### §P Common skill folder layouts observed
- **Registry monorepo:** `skills/<author>/<skill-slug>/SKILL.md` + helpers (used by `openclaw/skills`)
- **Agent template bundles:** `agents/<category>/<agent-name>/{SOUL.md, bot.js, .env.example, Dockerfile, docker-compose.yml, package.json, README.md}` (mergisi/awesome-openclaw-agents)
- **Workspace kernel:** flat root of MD files (AGENTS, SOUL, IDENTITY, HEARTBEAT, MEMORY, TOOLS, USER, BOOTSTRAP, BOOT) + `memory/YYYY-MM-DD.md` + `checklists/`
- **One-repo-per-skill:** bare repo with top-level SKILL.md (e.g., `nebius-skill`, `skill-devops-agent`)

**Our `deploy-X.md` flat naming is unique to us.**

### §Q Skill file structure pattern
Front-matter is flat key:value (NOT fenced YAML) with: `name`, `description`, `metadata` (author), `emoji`, `requires` (tools, comma- or space-separated).

Body flow: Title (H1) → Quick Start → Workflow Steps (table) → Commands → Example Session → State Management (JSON schema block) → Requirements (table) → Configuration → Notes.

### §R Identity / Soul / Heartbeat patterns
- SOUL.md = internal persona, values, tone (what model embodies)
- IDENTITY.md = external presentation (what users see)
- HEARTBEAT.md = periodic checklist; default 30 min, accepts cron expressions like `0 * * * *`
- AGENTS.md = boot sequence, rules, checklist routing
- **Minimum viable workspace = AGENTS + SOUL + TOOLS**
- MEMORY.md security gate: never load in group chats or sub-agents (boot must enforce)

### §S Naming conventions
- Slugs: kebab-case, verb-first or noun-scoped (`deploy-agent`, `vercel-deploy-claimable`)
- Author-namespaced in registry: `<author>/<slug>/`
- Subcommands: `<skill> <action> <arg>`
- State files: `{deployment-name}.json`
- Workspace files: `SHOUTING.md`; folders lowercase

### §T Cron / scheduling patterns
- Two-tier: heartbeat (batched, contextful) vs cron (precise, isolated)
- Heartbeat default 30 min
- Cron jobs published as copy-paste units in showcases
- "Recent OpenClaw release broke cron" tweet (Shpigford) — pin versions when auditing

### §U Model config patterns
- `~/.openclaw/openclaw.json` with `{ agent: { model: "<provider>/<model-id>" } }`
- Failover list + auth-profile rotation
- Ollama-first local routing for heartbeat/crons is consensus cost pattern

### §V Anti-patterns observed across repos
- Don't expose gateway port 18789 to internet
- Don't store API keys in VCS
- Don't install unverified ClawHub skills
- Don't run unattended agents without heartbeat monitoring or budget caps
- Don't load MEMORY.md in group chats / sub-agents
- Don't fan out cron jobs — collapse into heartbeat
- (No explicit "old way" deprecations in repos — diff against the LATEST canonical skills, not older entries)

---

## §W Anthropic models — pointer (sweep-table-only policy)

**This baseline intentionally does NOT carry current-model specs.** That list drifts faster than the baseline is updated, so current IDs / context / pricing live in exactly two places:

- **Vendor (authoritative):** [`platform.claude.com/docs/en/docs/about-claude/models/overview`](https://platform.claude.com/docs/en/docs/about-claude/models/overview) and [`platform.claude.com/docs/en/about-claude/pricing`](https://platform.claude.com/docs/en/about-claude/pricing)
- **Tier routing (which Claude for which call):** [`skills/deploy-model-routing.md`](../skills/deploy-model-routing.md)

§X below is this baseline's actual job: the **deprecation sweep table** audits use to catch stale model IDs.

---

## §X Anthropic deprecated/retired models (CRITICAL — sweep these)

| Model ID | Deprecated | Retires | Replace with |
|----------|-----------|---------|--------------|
| `claude-opus-4-20250514` | 2026-04-14 | **2026-06-15** | `claude-opus-4-7` |
| `claude-sonnet-4-20250514` | 2026-04-14 | **2026-06-15** | `claude-sonnet-4-6` |
| `claude-3-haiku-20240307` | 2026-02-19 | **2026-04-20 (TOMORROW)** | `claude-haiku-4-5-20251001` |
| `claude-3-5-haiku-20241022` | retired | 2026-02-19 | `claude-haiku-4-5-20251001` |
| `claude-3-7-sonnet-20250219` | retired | 2026-02-19 | `claude-sonnet-4-6` |
| `claude-3-5-sonnet-20240620/-20241022` | retired | 2025-10-28 | `claude-sonnet-4-6` |
| `claude-3-opus-20240229` | retired | 2026-01-05 | `claude-opus-4-7` |
| `claude-3-sonnet-20240229` | retired | 2025-07-21 | `claude-sonnet-4-6` |
| `claude-2.0`, `claude-2.1`, `claude-instant-*`, `claude-1.*` | retired | various | `claude-opus-4-7` / `claude-haiku-4-5` |

**Priority 1:** `claude-3-haiku-20240307` retires TOMORROW (2026-04-20). Any skill referencing this WILL break tomorrow.

---

## §Y OpenAI / Codex models — pointer (sweep-table-only policy)

**This baseline intentionally does NOT carry current-model specs.** Current IDs / context / pricing live in exactly two places:

- **Vendor (authoritative):** [`developers.openai.com/api/docs/models/all`](https://developers.openai.com/api/docs/models/all) and [`developers.openai.com/codex/models`](https://developers.openai.com/codex/models)
- **Tier routing, cost/auth strategy (OAuth subscription vs per-token), OpenRouter modes, and OpenClaw runtime resolution bugs (`gpt-5.4` → Unknown model):** [`skills/deploy-model-routing.md`](../skills/deploy-model-routing.md)

§Z below is this baseline's actual job: the **deprecation sweep table**.

---

## §Z OpenAI deprecated models (sweep these too)

| Model ID | Shutdown | Replace with |
|----------|----------|--------------|
| `chatgpt-4o-latest` | 2026-02-17 | `gpt-5.1-chat-latest` |
| `codex-mini-latest` | 2026-02-12 | `gpt-5-codex-mini` |
| `gpt-4-0314` / `gpt-4-1106-preview` / `gpt-4-0125-preview` | 2026-03-26 | `gpt-5` or `gpt-4.1` |
| `dall-e-2` / `dall-e-3` | 2026-05-12 | `gpt-image-1` / `gpt-image-1-mini` |
| `gpt-4o-realtime-preview*` | 2026-05-07 | `gpt-realtime-1.5` |
| `gpt-4o-audio-preview*` | 2026-05-07 | `gpt-audio-1.5` |
| Assistants API | 2026-08-26 | Responses + Conversations API |
| `o1-mini` | 2025-10-27 | `o4-mini` |
| `o1-preview` | 2025-07-28 | `o3` |
| `gpt-4.5-preview` | 2025-07-14 | `gpt-4.1` |
| `gpt-3.5-turbo*`, `babbage-002`, `davinci-002` | 2026-09-28 | `gpt-5.4-mini` / `gpt-5-mini` |

---

## §BB Model ID search/replace table (for the Codex strip pass)

### Anthropic
| If skill contains | Replace with |
|-------------------|--------------|
| `claude-opus-4.7`, `claude-opus-4_7`, `claude-4.7-opus` | `claude-opus-4-7` |
| `claude-opus-4.6`, `claude-opus-4_6` | `claude-opus-4-6` (or upgrade to `claude-opus-4-7`) |
| `claude-sonnet-4.6`, `claude-sonnet-4_6` | `claude-sonnet-4-6` |
| `claude-haiku-4.5`, `claude-haiku-4_5` | `claude-haiku-4-5` (alias) or `claude-haiku-4-5-20251001` |
| `claude-3-opus-20240229` | `claude-opus-4-7` (original retired 2026-01-05) |
| `claude-3-sonnet-20240229` | `claude-sonnet-4-6` |
| `claude-3-5-sonnet-20240620/-20241022` | `claude-sonnet-4-6` |
| `claude-3-7-sonnet-20250219` | `claude-sonnet-4-6` |
| `claude-3-haiku-20240307` | `claude-haiku-4-5-20251001` (PRIORITY — retires TOMORROW) |
| `claude-3-5-haiku-20241022` | `claude-haiku-4-5-20251001` |
| `claude-opus-4-20250514` | `claude-opus-4-7` (retires 2026-06-15) |
| `claude-sonnet-4-20250514` | `claude-sonnet-4-6` (retires 2026-06-15) |

### OpenAI / Codex (CRITICAL: `gpt-5.4-codex` is FICTIONAL)
| If skill contains | Replace with |
|-------------------|--------------|
| **`gpt-5.4-codex`** (NOT a documented model) | **`gpt-5.3-codex`** (current Codex flagship) |
| `codex-mini-latest` | `gpt-5-codex-mini` |
| `gpt-4o`, `gpt-4o-mini` | `gpt-5.4` / `gpt-5.4-mini` (API still works but UI retired) |
| `gpt-4-0314` / `gpt-4-1106-preview` / `gpt-4-0125-preview` | `gpt-5` or `gpt-4.1` (API retires 2026-03-26) |
| `gpt-4-32k`, `gpt-4-vision-preview`, `gpt-4.5-preview` | `gpt-5.4` or `gpt-4.1` |
| `o1-mini`, `o1-preview` | `o4-mini`, `o3` |
| `chatgpt-4o-latest` | `gpt-5.1-chat-latest` |
| `dall-e-2`, `dall-e-3` | `gpt-image-1` / `gpt-image-1-mini` |
| `gpt-3.5-turbo*`, `babbage-002`, `davinci-002` | `gpt-5.4-mini` |

### Notes on the replace tables

These tables are **stale-ID → current-family** only. They intentionally do NOT tell you which current model to pick for a given call type — that's routing, not sweep.

- For tier routing (which current model for which call type), see [`skills/deploy-model-routing.md`](../skills/deploy-model-routing.md).
- For OpenClaw runtime resolution bugs (`openai-codex/gpt-5.4` reporting as Unknown, 266k context truncation), see the "Platform caveats" section of the same skill.
- For fictional-ID research trail (why `gpt-5.4-codex` was flagged in the April 2026 sweep, which IDs were wrongly suspected), see [`audit/skill-audit-2026-04-19-codex-review.md`](./skill-audit-2026-04-19-codex-review.md).

If an audit finds a stale ID not covered by these tables, update the table — don't add a "current models" column. Current-model specs drift; stale→family mappings don't.

---

## §4C Live ground truth from Mac Mini 4C (probed 2026-04-19)

### §4C.1 Version
- `OpenClaw 2026.4.15 (041266a)` — exactly matches latest stable per §1

### §4C.2 Workspace at `~/.openclaw/workspace/`
Files present:
- `AGENTS.md` (7.8K, last edited 2026-04-04)
- `BOOTSTRAP.md`, `HEARTBEAT.md`, `IDENTITY.md`, `MEMORY.md`, `SOUL.md`, `TOOLS.md`, `USER.md`
- `memory/` (mode 700)
- `state/` (mode 700)
- `tmp/`
- `.openclaw/`
- `.git/` (workspace is a git repo)

All canonical workspace files present per §2.

### §4C.3 Cron API (live `openclaw cron --help`)
Subcommands: `add disable edit enable list rm run runs status` ✅ matches §3
- **No cron jobs configured yet** — `~/.openclaw/cron/jobs.json` does not exist (will be created by first `openclaw cron add`)

### §4C.4 Models API (live `openclaw models --help`)
Subcommands: `aliases auth fallbacks image-fallbacks list scan set set-image status` ✅ matches §4

### §4C.5 Skills (live `openclaw skills list`)
- **43/52 ready** out of bundled skills
- Bundled skills include: `1password`, `apple-notes`, `apple-reminders`, `bear-notes`, `blogwatcher`, `blucli`, `bluebubbles` (needs setup), `camsnap`, `clawhub`, `coding-agent`, etc.
- **`coding-agent` bundled skill** can delegate to Codex/Claude Code/Pi agents via background process — relevant for our `deploy-dev-team.md` and similar
- **`clawhub` bundled skill** wraps the npm `clawhub` CLI for skill install/update/publish

### §4C.6 Agent layout
`~/.openclaw/agents/main/agent/` contains: `auth-profiles.json`, `auth-state.json`, `models.json`
- Confirms canonical auth path per §2

### §4C.7 Subcommand inventory (live `openclaw --help`)
~30 top-level commands including: `acp`, `agent`, `agents`, `approvals`, `backup`, `capability`, `channels`, `clawbot` (legacy aliases), `completion`, `config`, `configure`, `cron`, `daemon` (legacy alias), `dashboard`, `devices`, `directory`, `dns`, `docs`, `doctor`, `exec-policy`, `gateway`, `health`, `hooks`, `infer`, `logs`, `mcp`, `memory`, `message`, `models`, `node`

Many of these are NEW since our internal authoring docs were written. **Significant.**

---

## §Local — audit of our own `_authoring/` docs (the rules-of-the-game)

### `_authoring/_skill-authoring-guide.md`
- **Hardcodes "Claude Opus 4.6"** as the operator (lines 9, 220) → must be Codex per §BB defaults
- References engagement-flavored "Standard Deployment Protocol in `_deploy-common.md`" — fine, but the philosophy section assumes operator-driven deployment over OAC, which is OAC's pattern, not OpenClaw-canonical SKILL.md format
- **Anti-patterns section is excellent** — lessons learned from real deployments, mostly evergreen
- Cross-platform reference table is solid
- New-skill template uses `# Deploy [System Name]` H1 — diverges from canonical SKILL.md `name: snake_case` frontmatter (§6, §Q)
- **Score:** YELLOW — solid foundation, needs Codex strip + a note about SKILL.md format vs `deploy-X.md` runbook format

### `_authoring/_deploy-common.md`
- 649 lines — most extensive, most stale
- **CRITICAL — falsely claims**: "openclaw cron does not exist in 2026.2.26" (line 587), "openclaw telegram does not exist in 2026.2.26" (line 588), "openclaw config set messaging.* messaging namespace does not exist in 2026.2.26" (line 589)
- These were TRUE for 2026.2.26 but are FALSE for 2026.4.15 (live verified §4C.3)
- "Phase 0.5 OpenClaw Compatibility Check" pattern is excellent (read version, list skills, verify subcommands exist) — this stays
- File transfer base64 standard is excellent
- Auth-profiles.json schema correct per §2 (auth lives in agents/<id>/agent/, not openclaw.json)
- Telegram client interaction patterns are heavy and OAC-focused — not all of this belongs in a generic `_deploy-common.md`
- **Score:** ORANGE — major rewrite needed. Many gotchas listed are now resolved. Compatibility-check pattern stays. Codex strip required.

### `_authoring/_skill-review.md`
- (Not yet opened — will read in audit Phase 2B)
- **Score:** _pending_

---

## Open questions (flagged for adversarial Codex review)

1. **Should we adopt canonical SKILL.md format?** Our `deploy-X.md` flat files are runbooks for an OPERATOR (over OAC) — they're not loaded into the agent context. ClawHub's SKILL.md format IS loaded by the agent. Are these complementary or competing? Reddit/GitHub research suggests both can coexist, but our skills wouldn't be ClawHub-publishable as-is.
2. **Is `deploy-personal-crm.md` (SQLite) obsoleted by ClawHub's `personal-crm` or `heleni-personal-crm`?** Worth comparing.
3. **Do we need our own `deploy-image-gen.md` or just install `nano-banana-pro`?** Same question for many content-gen skills.
4. **Heartbeat-vs-cron policy** — we should mandate "use heartbeat unless precise timing required" per §L community consensus. Our skills currently don't make this explicit.
5. **`deploy-fathom-pipeline.md` is Fathom-specific** — {{FAMILY_NAME}} uses Gemini for transcripts. Should base have a generic `deploy-transcript-pipeline.md` and Fathom be a variant?
6. **Skills that overlap with bundled** (`clawhub`, `coding-agent`, `apple-notes`, `1password`) — should our deploy-* skills wrap the bundled or replace them?

---

## Sources cited (for adversarial review)

### OpenClaw docs (`docs.openclaw.ai`)
- `/install/index.md`, `/install/node.md`
- `/concepts/agent-workspace.md`, `/concepts/soul.md`, `/concepts/model-providers.md`, `/concepts/model-failover.md`
- `/automation/cron-jobs.md`, `/automation/tasks.md`
- `/cli/cron.md`, `/cli/skills.md`, `/cli/models.md`
- `/tools/skills.md`, `/tools/creating-skills.md`, `/tools/clawhub.md`
- `/gateway/heartbeat.md`, `/gateway/configuration-reference.md`, `/gateway/sandboxing.md`
- `/reference/templates/IDENTITY.md`, `/SOUL.md`, `/HEARTBEAT.md`
- `/start/openclaw.md`, `/index.md`
- `https://github.com/openclaw/openclaw/blob/main/CHANGELOG.md`

### ClawHub
- `https://clawhub.ai` (search API: `/api/search?q=...`)
- ~25 individual skill listings (URLs in §B research notes)

### Reddit (5 verbatim, rest secondary)
- `r/openclaw/comments/1r0wks3`, `1rtpk8e`, `1rc06ee`, `1rlc7fr`
- `r/clawdbot/comments/1rs7yns`

---

## §Δ Phase deltas — Flyn Orchestrator build (2026-05-15 → 2026-05-17)

> Per-phase additions to the baseline. Each entry lists **new patterns** introduced by the phase (positive) and **new threats** surfaced (warnings). Future audits grade phases against the baseline AT THEIR SHIP DATE — not the latest. Entries are append-only.
>
> Naming convention: `§Δ.<phase-id>` where phase-id matches `ORCHESTRATOR-PHASE-RUBRIC.md` (0, 1, 1b, 2, 2c, 3, 4, 5, 6-partial, 7-partial, hygiene).

### §Δ.0 — MemoryRouter (PR #1, 2026-05-15)

**New patterns:**
- 5-tier memory hierarchy (hot / warm×2 / cool / cold / lesson) with declarative routing rules per importance
- 12-class secret redactor (`flyn_memory_router/redact.py`) called on every outbound Graphiti + workspace write
- Hot-tier `MEMORY.md` pin with 24h/72h decay + Owner-only permanent pin policy
- Passthrough mode for migration (`FLYN_MEMORY_ROUTER_PASSTHROUGH=true` preserves legacy direct-Graphiti writes during dual-write verification)
- `(source, dedup_key)`-namespaced dedup — replay blocks correctly without preventing same-key writes from different sources

**New threats:**
- **Redact-list-of-dicts bug** (T03) — when a payload field was a list of dicts, the recursive redactor skipped over the contained string fields. Fix landed before merge but worth knowing — recursive redactors should also descend into list elements, not just dict keys.
- **Hot-TTL-uses-last-updated semantics** (T12) — naive implementations use `created_at` for decay; correct semantics is `last_updated_at` so a pinned-then-re-mentioned memory doesn't decay early.

### §Δ.1 — Orchestrator foundation (PR #2, 2026-05-15)

**New patterns:**
- `claude -p --output-format stream-json --verbose` as the canonical worker invocation (stream-json without `--verbose` silently exits 0-byte; see `KNOWLEDGE/15`)
- **Fresh-context reviewer** — every PR review spawns a NEW `claude -p` invocation with diff-only context, structured `ReviewFindings` JSON output. This is Flyn's stated differentiator vs community tools that reuse the builder's context window.
- SQLite WAL-mode `state.db` with append-only `task_events` for the state-machine spine (`INBOUND → TRIAGING → ROUTED → DECOMPOSED → DISPATCHED → RUNNING → REVIEWED → DELIVERABLE_READY`)
- Worktree-per-task isolation: each task allocates its own git worktree under `~/.flyn/orchestrator/workspaces/<task_id>/`
- Cost tracking by parsing `usage` events directly from the stream-json output (no post-hoc API tally needed)

**New threats:**
- **0-byte capture without `--verbose`** — Phase 1 e2e overnight: missing `--verbose` caused workers to silently exit with 0-byte stream output. The orchestrator happily advanced to `deliverable_ready` with no actual deliverable. Hardened in Phase 1b.1.
- **Worktree stale state** — leaving an orphan branch (e.g., `flyn/T-0001`) from a prior crashed run caused subsequent `worktree add` to fail immediately. Hardened in Phase 1b.3 via `git worktree prune` + force-delete-orphan-branch.

### §Δ.1b — Orchestrator hardening (PR #3, 2026-05-15)

**New patterns:**
- **Silent-failure defenses** (4 of them): 0-byte capture guard, empty-diff defense, worktree idempotency, OAuth-as-API-key fallback
- **Sanitizer allowlist** format — specific files can allow specific pattern classes with justification; eliminates false-positive review noise for legitimate strings like `--dangerously-skip-permissions` or `api.telegram.org`
- **Mid-stream worker-kill on `BudgetExceeded`** — `CostTracker.add()` raises mid-`Popen` to abort the worker rather than letting it run to completion past the cap
- **3-tier auth model** (`Owner` / `Teammate` / `Other`) — `sender_role` on `TaskRecord` gates everything downstream; rolled into `IDENTITY.md` + `AGENTS.md` workspace edits
- "Spawned workers are tool processes, not peer agents" rule in `AGENTS.md` (post-compaction survival heading)

**New threats:**
- **`sk-ant-oat-*` token mistakenly passed as `ANTHROPIC_API_KEY`** — the `auth-profiles.json` slot stores BOTH OAuth refresh tokens AND API keys under the same key. The naive loader returned whatever it found. Fix: prefix check on `sk-ant-api*` only. See `KNOWLEDGE/21`.
- **OAuth contention with interactive Claude Code sessions** — headless `claude -p` workers share `~/.claude/.credentials.json` with interactive sessions; concurrent token refresh races log the operator out. Mitigation: pass `ANTHROPIC_API_KEY` (API-key auth bypasses the refresh path) when available. See `KNOWLEDGE/17`.

### §Δ.2 — Dev workflow (PR #4, 2026-05-15)

**New patterns:**
- `gh` CLI wrapper (`flyn_orchestrator/pr.py`) with `create_pr`, `merge_pr`, `pr_number_from_url` — keep `gh` calls behind a single module for easy mocking
- Per-project Telegram forum topics (`#dev-<slug>`) via `createForumTopic` API; slug cache at `~/.flyn/orchestrator/telegram_topics.json`
- File-domain `LockManager` (`flyn_orchestrator/locks.py`) — prevents two parallel builders editing overlapping globs in the same task
- Walk-me-through PR generator (`flyn_orchestrator/walkthrough.py`) — fresh-context explainer for non-technical reviewers
- Stale-PR nudge: daily heartbeat detects PRs waiting > 2 days, posts Telegram reminder

**New threats:**
- **Direct push to `main` is now a real risk** — Phase 2 wires up `gh`, so the orchestrator has push rights. Branch protection on `main` is criterion 2.8, enforced via PR-only flow. Verify branch protection on every new repo before connecting it.

### §Δ.2c — Router refactor (PRs #8 + #9, 2026-05-16)

**New patterns:**
- **Function-based phase runners** (`research_phase.py` / `content_phase.py` / `ops_phase.py` / `dev_phase.py`) — each workflow's state-machine logic lives in module-level functions taking `(task, services)`
- Frozen `PhaseServices` dataclass — 11-field bundle (store, memory, channels, reviewer_invoker, transition, safe_transition, notify, backend_registry, scratch_root, repo_path_for_workflow, workflows_dir) — replaces threading 8+ individual args through every signature
- Pure structural refactor verification: all pre-existing tests pass byte-for-byte unchanged (190 → 192 with the new `PhaseServices` tests)

**New threats:**
- **Cross-module mock patching** — `@patch("oldmodule.fn")` decorators silently miss the call site that now lives in a new module after extraction. Fix: patch the new module's namespace too, OR import helpers via module-namespace (`from . import pr as _pr`) so test patches at the helper's home module still intercept. See `KNOWLEDGE/18`.
- **Test→private-API coupling** — `test_critical_tier_owner_only` called `_handle_ops_approval` directly, forcing a 24-line shim through Phase 2c. Lint hint worth adding: forbid `router._<name>(` in test files. See `KNOWLEDGE/19`.

### §Δ.3 — Research workflow (PR #5, 2026-05-15)

**New patterns:**
- **Parallel-researcher orchestration** via `ThreadPoolExecutor` capped at 4 — N sub-questions decomposed by PM, run concurrently, results merged by Synthesizer
- **Fresh-context Critic** — separate `claude -p` invocation audits combined output for unsourced claims, contradictions, bias, gaps; critical/important findings transition to `CHANGES_REQUESTED` (not `DELIVERABLE_READY`)
- Citation extraction + URL fetch + timestamp recording at write time (rather than post-hoc)
- Raw-notes preservation alongside synthesized report at `~/Work/research/<topic>/<date>-<slug>.md` + `raw/` JSON dir

**New threats:**
- **Stub-routing-on-prompt-content trap** — a stub backend routed worker calls based on `"Critic" in prompt`, but the critic prompt itself contains both "Critic" and "Researcher" → wrong stub fired. Always route stubs on `spec.role` enum, not prompt-text substring.

### §Δ.4 — Content workflow (PR #6, 2026-05-15)

**New patterns:**
- **Draft-only-by-default** — content workflow NEVER auto-publishes regardless of intent ambiguity. `wants_send=False` → `DELIVERABLE_READY` with `📝 DRAFT:` prefix posted to channel; explicit "send via X" → `FINAL_APPROVAL_PENDING` → teammate-or-owner approval → channel adapter sends
- 8-phase sequential pipeline with conditional steps (Spec / Draft / Edit / [Fact-check?] / [Humanize?] / Format / Write / Decide-state)
- Per-platform formatting (`telegram`/`email`/`slack`/`plain`/`tweet`/`linkedin`/`markdown`) with platform-specific warnings (e.g., "draft exceeds Telegram 4096-char limit")
- Fact-checker scoped to **factual claims only** (numbers, names, dates) — opinions labeled as opinion, not flagged

**New threats:**
- Content-send-deferred for non-Telegram platforms — the MVP only ships Telegram outbound; sending to email/Slack/LinkedIn logs a `content_send_deferred` memory event and transitions to `COMPLETED` without actually sending. Operator must manually copy/paste. Will become a silent-failure risk once Phase 6 (multi-channel) ships and the operator EXPECTS email to send.

### §Δ.5 — Ops workflow (PR #7, 2026-05-15)

**New patterns:**
- **Declarative risk-rules YAML** (`workflows/ops/risk-rules.yaml`) — adding a rule = editing YAML, never code. The classifier loads + ranks by tier.
- **One-way escalation** via `max_tier(llm_tier, rule_floor)` — the LLM can upgrade a rule-classified tier but NEVER downgrade. The rule floor wins. Enforced at the orchestration layer (in `ops.classify_risk`), not just router-level — bypassing the router still can't downgrade.
- SHA256 audit snapshots typed by target kind (file / http / cmd) — file is SHA256 of bytes; http hashes response body; cmd records exit_code + stdout hash
- **Tier-keyed approval gates** — low: auto-execute; medium-high: owner-or-teammate approval; critical: owner-only AND written rationale (empty rationale → `ValueError`)
- `audit_log` table with `UNIQUE(task_id, action, ts)` — append-only, replay-duplicate-blocking; rationale stored in `payload` JSON for forensic review

**New threats:**
- **Dry-run side effects** — `ops.dry_run_action` runs an LLM-described dry-run, but if the executor prompt is sloppy, the LLM can still touch the target. Mitigation lives in the `executor.md` prompt + `dry_run_supported` field on `OpsSpec`. Spec authors must mark `dry_run_supported=False` if the action is non-rehearsable.
- **Validator post-condition trust** — `ops.validate_action` accepts the validator's "passed" verdict at face value. A compromised validator prompt could green-light a botched execute. Conformance to validator role-prompt is critical; deferred to a future audit phase.

### §Δ.6-partial — Email channel (PR #13, 2026-05-17)

**New patterns:**
- **RFC 8601 `Authentication-Results` parser** + allowlist override (`email_auth.py`). Strict policy: BOTH spf and dkim must NOT explicitly fail; at least ONE must explicitly pass; no auth headers → reject (unless allowlisted).
- Subject-line tagging convention (`[FLYN-TASK]`, `[FLYN-REPLY:<task_id>]`, `[FLYN-APPROVE:<task_id>]`, `[FLYN-REJECT:<task_id>]`) for disambiguating new tasks vs follow-ups vs approval responses
- **Prompt-injection detection catalog** (`injection_detect.py`) — 8 regex patterns (instruction-override / role-reassignment / role-confusion-tag / system-prompt-reference / new-instructions-injection / prompt-boundary / zero-width-unicode / base64-blob / excessive-whitespace). Inbound emails with `(suspicious=True)` are rejected before they reach the PM-LLM.
- Allowlist-first auth — Ryan/Beth/Eric always pass regardless of SPF/DKIM verdict; external senders must have at least one passing auth header

**New threats:**
- **Allowlist hardcoded vs sourced from CONTACTS.md** — MVP has the allowlist hardcoded in `email.py:DEFAULT_ALLOWLIST`. When CONTACTS.md becomes the source of truth (Phase 1b.6 wired it for auth tiers but not for email allowlist), keep the two in sync OR delete the hardcoded fallback.
- **Injection patterns are a moving target** — the catalog is regex + pattern-matching. Real attackers will route around it (Unicode confusables, multi-step prompts, encoded payloads). Treat as defense-in-depth, not a wall. Consider adding an LLM-based scanner pass in Phase 6b.

### §Δ.7-partial — Multi-PM adapters (PR #11, 2026-05-16)

**New patterns:**
- **PMAdapter Protocol contract** formalized at `adapters/base.py` — 4 methods (`create_task`, `update_state`, `link_artifact`, `comment_on_task`); each implementation conforms via `@runtime_checkable` isinstance
- **Adapter never raises** best-effort guarantee — formalized in `test_pm_adapter_conformance.py` with `pm_adapter_with_failing_http` fixture that injects an exception-raising HTTP; every method must return cleanly. See `KNOWLEDGE/20`.
- **Generic webhook PMAdapter** as a pattern for future PM-system integrations — configurable target URL + optional `X-Flyn-Secret` header; subclassing is for special semantics only
- Shared stdlib `_http.py` urllib helper — no new HTTP-client dependency; injectable for tests; supports custom headers

**New threats:**
- **OL wiki has no native task-state field** — `update_state` is a no-op for `OLWikiPMAdapter`. The Decision row remains as the durable artifact, but state transitions are not mirrored. If/when OL wiki adds a `tasks` table, revisit.
- **PMAdapter `configured` is advisory** — adapters can stub-return even when `configured=True` (e.g., HTTP layer failed). Callers should not trust `configured` alone; observe return values.

### §Δ.hygiene — Cross-cutting docs (PR #12, 2026-05-16)

**New patterns:**
- 4 KNOWLEDGE entries added (18 cross-module-mock-patching, 19 test-public-API-not-internals, 20 adapters-never-raise, 21 OAuth-vs-API-key-token-discrimination)
- Retroactive `CHANGELOG.md` in Keep-a-Changelog format, PR-numbered releases instead of semver
- `RESUME-HERE.md` orchestrator section at top with live-service table + auth-contention warning + pending ship-gate list

**New threats:**
- **Rubric drift** — the rubric audit (PR #10) found 5 separate drift bugs (Phase 1 rows still ⬜ after merge, Phase 5 aggregate/detail mismatch, Phase 6 copy-paste error claiming 8/8 when 0/8, cross-cutting count off-by-one, overall denominator off after n/a exclusion). Score lines and row counts diverge silently. Mitigation: audit the rubric after every major phase merge.

### §Δ.1.8 — Watchdog (2026-05-18)

**New patterns:**
- **Polling-based stuck-worker triage** — a daemon thread wakes every N seconds (default 30s), reads the last K bytes of the capture file, and calls a TriageBackend. No re-architecture of the dispatcher stream loop required.
- **Pluggable `TriageBackend` Protocol** — `OllamaTriageBackend` (gemma4:e4b, HTTP POST to local Ollama) is the default; tests use a `StubTriageBackend`. Swap by passing any object with a `classify(tail, intent, elapsed) → TriageResult` signature.
- **Verdict → action callbacks** — `on_nudge`, `on_stuck`, `on_done`, `on_escalate` are optional callables injected at construction; default is no-op. Callers wire SIGTERM or Telegram notifications; the Watchdog itself has no subprocess reference.
- **Consecutive-STUCK hysteresis** — `consecutive_stuck_threshold` (default 2) prevents false positives from a single slow poll. ESCALATE bypasses the threshold for catastrophic states.
- **Opt-in dispatcher integration** — `WorkerDispatcher.dispatch()` accepts `watchdog: Optional[Watchdog] = None`; start/stop are bracketed around the backend call in a try/finally so the thread is always joined. Existing callers passing no watchdog see zero behaviour change.

**New threats:**
- **Triage backend errors silently default to FINE** — an unreachable Ollama, timeout, or malformed JSON response logs a warning but returns `FINE`. This means real stuckness can go undetected if the triage backend is degraded. Mitigation: monitor `confidence=0.0` verdicts in the audit log.
- **Thread leakage if dispatcher crashes before stop()** — the daemon thread is started before `backend.run()` and stopped in the finally block. If the finally block itself throws (extremely unlikely), the daemon thread will outlive the task and continue polling a stale capture file. Since it's a daemon thread it won't prevent process exit, but it will consume CPU until the process ends.
- **First-poll always empty** — the capture file is empty at t=0; the Watchdog skips classify on empty tails. This is correct but means a genuinely fast-STUCK worker gets a free pass for the first interval.

### §Δ.3b — Research auto-rerun on critic block (PR #19, 2026-05-18)

**New patterns:**
- **Auto-retry-with-context loop on workflow critique failure** — research workflow's critic-block path is no longer a dead-end. On first failure, the orchestrator builds a "Critic findings from previous research run" markdown block from blocking findings and re-runs researchers with that text appended to their prompts. If the retry critique passes, the workflow proceeds to synthesize as if it had passed the first time.
- **`extra_context` kwarg on `run_researchers`** — opt-in parameter that gets appended (separated by `---`) to each researcher's prompt. Doesn't change semantics when None.
- **Distinct `actor` field for retry transitions** — `task_events`' UNIQUE(task_id, from_state, to_state, actor) constraint is satisfied by using `actor="research-retry"` for retry-cycle transitions. Each retry cycle re-emits DISPATCHED→RUNNING→REVIEWED with the retry actor.
- **Retry-count + blocking-findings on task payload** — when retry-then-still-fails, the payload records `research_retry_count=1` and `research_blocking_findings=[{severity, category, note}, ...]` so a human reviewer at CHANGES_REQUESTED can see exactly what's blocking.

**New threats:**
- **Capped at 1 retry by hardcoded path** — there's no config knob; if you want a 2-retry workflow you have to edit `research_phase.py`. Tradeoff: avoids runaway critique loops on adversarial intents.
- **Retry shares the same scratch dir** — each researcher's worker_dir is `scratch_dir / sub_q["id"]`, identical between initial run and retry. The retry overwrites the initial capture file. If a future audit needs to inspect both runs' captures, we'll need to namespace by run-attempt.
- **Critic prompt receives the same outputs format on retry** — the second critique sees the new researcher outputs but doesn't know they're a "retry attempt". A more sophisticated retry might tell the critic explicitly to grade against the prior findings.

---

*Per-phase deltas added 2026-05-17 by Claude Opus 4.7 — closes cross-cutting criterion X.2. Going forward, each phase's PR adds its own `§Δ.<phase-id>` subsection here at merge time.*
- Aggregators: `kilo.ai`, `managemyclaw.com`, `simen.ai`, `florian-darroman.medium.com`

### GitHub
- `openclaw/openclaw`, `openclaw/clawhub`, `openclaw/skills`
- `VoltAgent/awesome-openclaw-skills`, `mergisi/awesome-openclaw-agents`
- `win4r/openclaw-workspace`, `digitalknk/openclaw-runbook`
- `prompt-security/clawsec`, `colygon/nebius-skill`

### Models
- `https://platform.claude.com/docs/en/docs/about-claude/models/overview`
- `https://platform.claude.com/docs/en/docs/about-claude/model-deprecations`
- `https://developers.openai.com/api/docs/models/all`
- `https://developers.openai.com/api/docs/deprecations`
- Individual model pages for `gpt-5.4`, `gpt-5.4-pro`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.3-codex`, `gpt-5-codex`

### Live probes
- Live Mac Mini probe — version, CLI surface, workspace layout, skills list, cron status — all 2026-04-19
