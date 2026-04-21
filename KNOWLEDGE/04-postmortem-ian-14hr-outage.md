---
name: postmortem-ian-ferguson-2026-04-17
description: Post-mortem of the Ian Ferguson bot outage (2026-04-17 to 2026-04-18) — ~14 hours operator time, three-layer root cause (TLS fingerprint + models.json URL corruption + missing channels section after restore), five memory artifacts produced for future-proofing.
type: project
originSessionId: 0eaf92c3-b027-400b-b564-f087026dba75
---
# Post-Mortem: Ian Ferguson Bot Outage — 2026-04-17 → 2026-04-18

## TL;DR

Ian's Telegram bot (@Vishnuexu_bot) stopped responding. Diagnosis took ~14 hours of operator time across two days because the root cause was three layers stacked on top of each other, and the error surfaced as a misleading "DNS lookup failed" message that sent us down multiple wrong paths. Eventually traced to: (1) Homebrew Node 25's TLS fingerprint being flagged by Cloudflare on chatgpt.com's backend-api, (2) OpenClaw 2026.4.14's corrupted `models.json` with a bogus `/v1/` URL path and wrong provider key, (3) post-restore `openclaw.json` missing the `channels` section entirely. All three resolved 2026-04-18. Produced five reusable memory artifacts. Added local Gemma 4 routing as bonus to shift ~2.7M tokens/month off OpenAI.

## Timeline

| When | Event |
|---|---|
| Weeks prior | Ian's install on 2026-03-02 was pre-regression, working fine on older stack |
| ~2026-04-14 | OpenClaw 2026.4.14 auto-updated on Ian's Mac; introduced codex-provider regression |
| 2026-04-16 ~12:00 | Ryan reports "bot not responding" — initial triage begins |
| 2026-04-16 ~14:00 | First misdiagnosis: blamed corrupted OAuth, did a `openclaw models auth login` — no change |
| 2026-04-16 ~14:30 | Second misdiagnosis: suspected rate limit on Codex account — ruled out via `/accounts/check` returning 200 |
| 2026-04-16 ~15:00 | First reset of `~/.openclaw/` — Ian re-authed — no change |
| 2026-04-16 ~16:00 | Suspected Cloudflare block; tried cellular hotspot → same error → ruled out IP |
| 2026-04-16 ~16:30 | Second reset (wiped our progress); started over |
| 2026-04-16 ~17:00 | Hypothesis: Node version TLS fingerprint — installed Node 22 LTS tarball, pulled OpenClaw 2026.4.12; Cloudflare wall DEFEATED (clean JSON 404 instead of HTML 403) |
| 2026-04-16 ~17:30 | New error `{"detail":"Not Found"}` on every codex model — diagnosed as corrupt `models.json` (`/v1/` suffix + wrong provider key) |
| 2026-04-16 ~17:45 | Fixed models.json → LLM pipeline proven working end-to-end (`reply OK`) |
| 2026-04-16 ~18:00 | Saved memory artifacts documenting root cause; paused session |
| 2026-04-17 ~12:00 | Second session: Ian's agent offline — full clean reinstall needed because `openclaw.json` was wiped again |
| 2026-04-17 ~14:00 | New enrollment `ad3d1548-…` after hard-deleting stale D1 row (`wrangler d1 execute DELETE`) |
| 2026-04-17 ~14:19 | Discovered post-restore `channels` section missing from `openclaw.json` → fixed via `openclaw channels add`; `delivery-recovery` then flushed 2 backlogged Telegram replies to Ian |
| 2026-04-18 ~15:00 | Proven fully live: Vishnu sent personality-matched LLM message to Ian via Telegram |
| 2026-04-18 ~15:30 | Bonus phase: added local Gemma 4 via tmux-detached pull, wired heartbeat + 3 crons to local model |
| 2026-04-18 ~16:05 | All work saved to memory; done |

## Root Causes (Three Layers)

### Layer 1: Cloudflare TLS fingerprint on chatgpt.com backend-api

Ian's Mac had Homebrew Node 25.9.0 running the OpenClaw agent. Node 25 is brand new (released ~Feb 2026), and its bundled undici's TLS ClientHello has a JA3/JA4 fingerprint that Cloudflare's bot-mitigation layer scores as non-browser traffic. The chatgpt.com `/backend-api/v1/codex/responses` endpoint has tighter policies than the homepage, and returned an HTML challenge page (HTTP 403) instead of the expected JSON response.

**Critical confounder:** OpenClaw's error classifier saw HTML where it expected JSON and defaulted to `"LLM request failed: DNS lookup for the provider endpoint failed"` — a completely misleading label for a Cloudflare block. We spent hours chasing DNS/network issues that didn't exist.

**Fix:** Tarball Node 22.15.0 LTS at `~/.local/node/` + OpenClaw 2026.4.12 (not 2026.4.14, which has a separate codex-provider regression). LTS Node has a TLS fingerprint that matches millions of legitimate production clients; Cloudflare whitelists it.

### Layer 2: Corrupted models.json from 2026.4.14

OpenClaw 2026.4.14 wrote `~/.openclaw/agents/main/agent/models.json` with two bugs:
- Provider key `"codex"` instead of `"openai-codex"`
- `baseUrl: "https://chatgpt.com/backend-api/v1"` with a bogus `/v1/` suffix that returns 404

Downgrading the binary to 2026.4.12 didn't auto-fix this file — it stayed corrupt and OpenClaw kept using it. Had to rewrite it manually with the correct structure.

### Layer 3: Missing channels section after restore

After the full clean reinstall + data restore, `openclaw.json` came back with memory/sessions/identity intact but the `channels` top-level key was entirely missing. Telegram polling failed silently; no replies to Ian's messages. Fixed by `openclaw channels add --channel telegram --token <preserved-bot-token>` which atomically reconstructs the channel block.

## What Cost Us Time (vs what saved time)

### Time sinks (retrospectively avoidable)

1. **The "DNS lookup failed" misnomer** burned the first 2 hours of diagnosis on network-layer checks that were never going to find anything. Lesson: when OpenClaw reports DNS failures on LLM calls, **always** curl the endpoint directly with `curl_cffi` + Chrome impersonation first to confirm what the server actually returns.
2. **Ryan ran two surprise resets** during my pauses, each wiping progress we'd just made. Lesson: when a session is mid-surgery, lock in fixes with clear "do not re-reset" annotations and make sure the collaborator has the checkpoint context.
3. **Trying to fix via OAuth re-login** was the instinctive first move; it was never going to help since the block was below the auth layer. Lesson: rule out the transport layer (network, TLS, Cloudflare) before touching auth.
4. **Attempting the openclaw `--deliver` agent CLI** for the test message wasted ~5 minutes on "Waiting for agent reply…" — that CLI path needs a pre-existing session; for operator-initiated messages, use `openclaw infer` + Telegram API directly.

### Speed-ups (worth repeating)

1. **Comparing against a known-good client (Brian)** was the diagnostic turning point. Confirming Brian's install worked on the same provider proved it was Ian-specific, not universal.
2. **Direct curl_cffi probe** of chatgpt.com with Chrome impersonation confirmed the TLS fingerprint hypothesis in 30 seconds once we thought to try it.
3. **tmux-detached pattern** for the Gemma 4 pull — pull would otherwise have required babysitting through three timeout cycles.
4. **Persistent memory handoff** between sessions meant 2026-04-18 Ryan didn't waste any time re-discovering state — the `project_ian_ferguson_install_state.md` note gave exact paths, IDs, and pending steps.

## Durable Artifacts Produced

Five memory files written for future clients + operators:

1. **`project_ian_ferguson_install_state.md`** — full install record for Ian
2. **`feedback_openclaw_cloudflare_node_fingerprint.md`** — diagnostic ladder for "LLM request failed DNS lookup" on codex
3. **`feedback_oac_enrollment_vs_session.md`** — the 3-step enrollment flow (session → enroll API → install script with launchd plist)
4. **`feedback_oac_tmux_long_running.md`** — tmux pattern for commands exceeding the OAC PTY 10-min timeout
5. **`feedback_openclaw_local_background_routing.md`** — cost-optimal model routing (cloud = user chats only; everything else = local Ollama)

## Preventive Measures / What We'd Do Differently

### On the client-install process
- **Default install should use tarball Node at `~/.local/node/` even for admin users** (not just non-admin). Homebrew auto-updates Node to bleeding-edge versions that may drift past Cloudflare's compatible-fingerprint range. Lock Node version explicitly.
- **Pin OpenClaw to a vetted version, not `@latest`** — 2026.4.14 would have been caught earlier if we'd held at 2026.4.12 until the regression shipped a fix.
- **Local heartbeat + local background routing should be the install-day default**, not a retrofit. Every new client gets `ollama pull gemma4:e2b` + routing configured during initial setup.

### On the debug process
- **Add a Cloudflare-fingerprint pre-check to `openclaw doctor`** — a 5-second `curl_cffi` probe that confirms `chatgpt.com/backend-api/*` accepts the current binary's TLS fingerprint. Would have diagnosed Ian's issue in the first 2 minutes instead of 2 hours.
- **Fix OpenClaw's error classifier** — "DNS lookup failed" when the server actually returned HTML 403 is user-hostile. Parse the HTML and detect Cloudflare challenge pages explicitly. (Worth proposing upstream PR.)

### On the memory/handoff process
- **"Do not re-reset" annotations** in mid-surgery project memories should be bold-prominent at the top, not buried in the bottom section. Cost us two partial rollbacks during this incident.

## Follow-ups for Other Clients

Every client currently running OpenClaw with Codex primary should be audited for:

1. **Node version** — if on Homebrew Node 25+, consider pre-emptive switch to tarball Node 22 LTS.
2. **`models.json` integrity** — check for the `/v1/` baseUrl bug; fix if found.
3. **Stale cron model refs** — grep `~/.openclaw/cron/*.json` for `anthropic/` or any other removed provider; flip to local Ollama.
4. **Channel section present** — after any restore, verify `openclaw config get channels` returns a populated object, not "Config path not found".
5. **Local heartbeat + background routing** — opportunistically add this to reduce token burn on any client that has Ollama installed but isn't using it for routing.

**Specifically:** Brian Greenleaf, Dan Caruso, Marshall Mosher, Paul Revas, Josh Vaughn's future clients. Worth scheduling a one-sweep audit pass where we SSH into each and run a quick probe script.

## Cost of the Incident

- Operator time: ~14 hours over 2 days (substantial)
- Ian-visible downtime: ~2 days from when he first noticed the bot was unresponsive
- User data lost: **zero** (full backups at `~/openclaw-keep-20260416-*/` on his Mac)
- Financial cost: minimal OpenAI spend during the failing attempts (most hit Cloudflare before using tokens); no other costs

## Net Outcome

- Bot back online, more resilient than before
- Two durable environmental protections (tarball Node, pinned OpenClaw version)
- Local Gemma 4 routing saves ~2.7M tokens/month going forward
- Five reusable memory artifacts benefit every future install
- **Ian's actual user data (memory, sessions, identity, Telegram) completely preserved**

Worth the 14 hours in terms of the durable fixes and lessons, but we'd want the preventive measures above to mean no future client goes through the same path.
