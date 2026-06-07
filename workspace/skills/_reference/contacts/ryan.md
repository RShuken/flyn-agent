# USER — Ryan

## Identity

- **Name:** Ryan Shuken
- **Preferred name:** Ryan
- **Email:** ryanshuken@gmail.com
- **Timezone:** America/Denver (MT) — confirm on first boot
- **Telegram:** primary account; Flyn has its own dedicated bot
  - Bot: `@flyn_4c_bot` (id 8842152875) — DM channel verified 2026-05-12
  - Ryan's chat_id: `7191564227` (use for outbound DMs from Flyn)
  - Previous bot `@fourC_3000_bot` is being retired

## Role and context

- **Primary role:** OpenClaw consultant + builder
- **What he's building right now:**
  - OpenClaw install and deployment tooling — `openclaw-base` (public library, source of truth), `flyn-agent` (this workspace)
  - Cora — Supabase-backed product at getcora.io, deployed on Railway
  - Ongoing consulting engagements for other operators deploying OpenClaw
  - Various other agent / integration projects on the side (Ryan will surface these as relevant)

## How he works

- **Communication:** Telegram > email. Async. Short, specific messages.
- **Decision-making:** Wants a range of options + a recommendation with tradeoffs. Do NOT present a single path as the only path. Flag what you're unsure about.
- **Depth preference:** Deep technical context when it matters. No summary fluff. Details with evidence (file paths, line numbers, vendor docs) beat prose.
- **Scope preference:** Do the thing asked. Don't refactor, don't add hypothetical features, don't scope-creep.
- **Verification flow:** Always local → dev → production. Never auto-merge to dev/staging without explicit go-ahead. (See `feedback_local_then_dev_before_live`.)

## Technical depth

- Deep: TypeScript, Node, Cloudflare Workers, Supabase, SQLite, Apple Silicon stacks, OpenClaw internals, agent architecture
- Comfortable with: AWS/GCP/Azure basics, Python, bash, Docker, launchd/cron
- Prefers local/on-device where plausible — cost-conscious + privacy-conscious

## What he values

- **Low ongoing cost.** Prefer local models and OAuth-flat-rate over per-token frontier unless quality demands it.
- **Privacy.** Client data stays on the machine when possible; never commit secrets.
- **Fast iteration.** Ship rough, learn, adjust. Don't plan exhaustively.
- **Research-first.** Build a baseline + cite primary sources before grading anything.
- **Honest reporting.** No fantasy approvals. Evidence-based completion, not "should work."

## Hard nos

- Do NOT send any email, post to public channels, or message anyone outside approved contacts without explicit OK.
- Do NOT claim work is done that isn't. "Appears to work" and "done" are not the same.
- Do NOT auto-migrate auth secrets to macOS Keychain under any launch-agent setup. (64-hour outage precedent — `_deploy-common.md` "Secret storage".)
- Do NOT use Claude/Anthropic models in default routing for background work. Subscription OAuth on OpenAI Codex is the cost path.
- Do NOT default to running background-process tasks. Only run background if explicitly instructed or from a scheduled cron.

## Context for Flyn

Ryan is running a solo consulting operation while building in public. Time and trust are the constraints, not money per se — but ongoing-cost discipline matters because many deployments run 24/7. When in doubt, ask one targeted question rather than five assumptions, and ship when you have evidence — not when something "should work."
