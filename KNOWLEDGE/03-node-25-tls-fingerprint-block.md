---
name: openclaw-cloudflare-node-fingerprint
description: OpenClaw 2026.4.14 + Homebrew Node 25 combo trips Cloudflare bot-mitigation on chatgpt.com/backend-api — error masquerades as "DNS lookup failed"
type: feedback
originSessionId: 0eaf92c3-b027-400b-b564-f087026dba75
---
## When an OpenClaw codex bot shows "LLM request failed: DNS lookup for the provider endpoint failed" with HTML rawError

The error label is misleading. It's actually Cloudflare returning a 403 challenge page from `chatgpt.com/backend-api/v1/codex/responses`. OpenClaw's error parser sees HTML where it expected JSON and defaults to "DNS lookup failed". DNS is fine.

**Why:** Ian Ferguson's Mac burned a full day on this. Multiple wrong diagnoses before we found the stacked root cause: (a) Homebrew Node 25 TLS JA3/JA4 fingerprint is flagged by Cloudflare as non-browser bot traffic, (b) OpenClaw 2026.4.14 has a separate regression that adds a bogus `/v1/` to the codex baseUrl AND uses provider key `codex` instead of `openai-codex`, (c) 2026.4.14 also sends `originator: pi` UA header that Cloudflare scores negatively.

**How to apply:** When ANY client's OpenClaw codex bot stops responding with this error pattern, run this diagnostic ladder BEFORE wiping state:

1. **Direct curl with Chrome fingerprint via curl_cffi** — confirms if the block is TLS-level vs account-level:
   ```python
   from curl_cffi import requests as cc
   r = cc.get("https://chatgpt.com/backend-api/me", headers={"Authorization":f"Bearer {tok}"}, impersonate="chrome124")
   # Expect HTTP 200 with user JSON if OAuth + fingerprint both OK
   ```

2. **Check Node version** — `node --version`. If Node 25+, this is probably the block:
   - **Install Node 22 LTS tarball** at `~/.local/node/` (matches what non-admin install path uses — known-good fingerprint)
   - Download: `https://nodejs.org/dist/v22.15.0/node-v22.15.0-darwin-arm64.tar.gz`

3. **Check `~/.openclaw/agents/main/agent/models.json`** — if it has provider key `codex` (not `openai-codex`) or `baseUrl: ".../backend-api/v1"`, the file was written by 2026.4.14 and is broken:
   - **Correct content:** `{"providers":{"openai-codex":{"baseUrl":"https://chatgpt.com/backend-api","api":"openai-codex-responses","models":[]}}}`

4. **OpenClaw version** — `openclaw --version`:
   - 2026.4.14 → broken (Cloudflare regression). Downgrade via `npm i -g openclaw@2026.4.12 --prefix=<node_path>` (use the tarball Node, not Homebrew)
   - 2026.4.15-beta.2 → also doesn't fix Cloudflare (release notes checked 2026-04-16)
   - 2026.4.9 → missing required `client_version` query param
   - **2026.4.12 is the sweet spot** on tarball Node 22 LTS

5. **Gateway launchd plist** at `~/Library/LaunchAgents/ai.openclaw.gateway.plist` must point to the tarball Node, not Homebrew Node. Use `plistlib` in Python to rewrite Program + ProgramArguments to `/Users/<user>/.local/node/bin/node` paths.

**What NOT to waste time on:**
- Re-logging Codex OAuth (auth is fine, fingerprint is the block)
- Cellular hotspot (IP isn't the block — TLS fingerprint is)
- Browser cookie clear (chatgpt.com UI cookies are unrelated to OpenClaw's chatgpt.com backend fingerprint)
- `~/.openclaw/` full wipe and reinstall (reinstall without Node+models.json fix gets you right back to the same 403)
- A local TLS-bypass proxy (curl_cffi proxy) — over-engineered; Node tarball swap is simpler
- Waiting for OpenAI account flag to clear — not an account issue

**Cross-reference:** See `project_ian_ferguson_install_state.md` for the full debug timeline and exact commands. Paul Revas was already on 2026.4.9 which is why his audit passed cleanly — he was on the pre-regression version.
