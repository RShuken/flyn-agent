# Lessons Learned — Intel Mac Deploy (Nicolas Aubert, 2026-04-25)

End-to-end deployment recap for any future Intel-Mac client. Companion to:
- `install/openclaw-install-intel.md` — install + onboard
- `install/memory-stack-intel.md` — memory architecture + components
- `deploy/memory-intel/graphiti-falkordb-smoke.py` — KG smoke
- `deploy/memory-intel/graphiti-kuzu-smoke.py` — Kuzu reproducer (broken upstream)

Every line below is something we hit live during Nicolas's deploy. **Read this before onboarding the next Intel Mac client** — it'll save 4+ hours.

---

## 1. Hardware envelope assumed

- Intel x86_64 (Mac mini Late 2014 `Macmini7,1`, 4 cores, 16 GB RAM, macOS 12.7.6)
- macOS 12.x is the latest version this hardware supports.
- **No Metal/MPS** — local LLM inference is out. Cloud-only.
- French locale (CEST) — log timestamps localized; `Sam` = Saturday in French. Be aware when grepping.

## 2. Account / admin reality

- Operator-side connection is via the OpenAgent Connect remote agent (launchd `com.openclaw.remote-agent`, x86_64 bundled node). Verified.
- The macOS user (`Didi`) was **NOT a local admin** for most of the deploy. Sudo prompts hang remote PTYs.
- Mid-deploy an admin elevated Didi *long enough* to install Xcode CLT + Homebrew, then Didi reverted to non-admin.
- **Rule:** assume non-admin until proven otherwise. Build everything user-prefix.

## 3. Install gotchas

| What broke | Why | Fix |
|---|---|---|
| `curl … install.sh \| bash` | Picks **npm** install path on macOS; auto-runs Homebrew installer; needs `sudo` | Use `install-cli.sh` (user-prefix Node + OpenClaw under `~/.openclaw/`) |
| `openclaw onboard --install-daemon --non-interactive` exits 0 with empty log | Hits a security-gate prompt that `--skip-ui` doesn't bypass | Add **`--accept-risk`**. Required pair: `--non-interactive --accept-risk` |
| `openclaw doctor --fix` hits interactive prompts | Same | `--non-interactive --yes` |
| `openclaw doctor` plugin-dep install fails | `node-edge-tts` (Microsoft TTS) and `libsignal-node` (WhatsApp/Signal) install via `git ls-remote` | Need Xcode CLT installed first (admin task) |
| `brew install python@3.12` stalls indefinitely on `openssl@3` dep | macOS 12 + brew is slow / sometimes deadlocks compiling deps | **Skip brew for Python.** Use `uv` (Astral): `curl -LsSf https://astral.sh/uv/install.sh \| sh`. Then `uv python install 3.12` finishes in <30 s with prebuilt cpython. |
| `git clone` for Homebrew | Triggers `xcode-select` GUI dialog needing admin | Either install Xcode CLT first, or use the tarball fallback (`curl … github.com/Homebrew/brew/tarball/master \| tar -xz`) — but the tarball is a shallow brew that can't tap or update. |

## 4. Workspace-pollution rule (high-leverage)

OpenClaw's built-in memory indexer crawls `~/.openclaw/workspace/` recursively. We originally put the mem0 venv inside `~/.openclaw/workspace/memory/mem0/venv/`. That inflated the index from **2 files / 2 chunks of real memory to 43 files / 97 chunks of pip vendor LICENSE files** (idna, MT19937, etc.). Searches for "Nicolas" returned BSD-3 license boilerplate.

**Rule:** any venv, `node_modules`, large data file, model checkpoint, Docker volume, or anything the agent shouldn't reason over goes under `~/.openclaw/data/`. Only human-edited markdown + memory belongs in `workspace/`.

Layout we converged on for Nicolas:

```
~/.openclaw/
├── workspace/               # indexed by openclaw memory
│   ├── *.md                 # IDENTITY, AGENTS, USER, etc.
│   ├── memory/*.md          # daily memory files
│   └── business_crm/        # the agent's CRM workspace (markdown + scripts)
├── data/                    # NOT indexed — bulky stuff lives here
│   ├── mem0/                # mem0 venv + chroma store
│   ├── structured/graphiti/ # graphiti venv
│   └── falkordb/            # FalkorDB Docker volume
├── extensions/lossless-claw/
├── tools/node-v22.22.0/
├── lib/node_modules/openclaw/
└── agents/main/agent/auth-profiles.json + auth-state.json
```

## 5. Model IDs that openclaw 2026.4.23 actually accepts

The error `Unknown model: gemini/gemini-2.5-flash` cost us a chat outage. Empirical:

| Provider namespace | ID format that works | Source for full list |
|---|---|---|
| `openai-codex/*` | `openai-codex/gpt-5.5`, `openai-codex/gpt-5-codex` (varies — check) | `openclaw capability model providers` then filter |
| Google (Gemini) | **bare** `gemini-2.5-flash`, `gemini-1.5-pro` | `openclaw capability model list` |
| Embeddings | `gemini-embedding-001` (NOT `models/text-embedding-004` from old SDK) | API: `generativelanguage.googleapis.com` |

**`gemini/gemini-2.5-flash` does NOT work** — that prefixed form is the bug. Use the bare ID.

The Flyn template at `templates/openclaw.json` uses `openai-codex/gpt-5.4-*` style IDs because that's the codex namespace; for Gemini drop the prefix.

Always lookup live with: `openclaw capability model list 2>&1 | grep -iE "gemini|codex"` before committing config.

## 6. Cooldown / rate-limit (critical for Pro subscriptions too)

The most painful single bug we hit:

- A **single** `rate_limit` failure on `openai-codex/gpt-5.5` triggered a **38-minute provider-wide cooldown**. All Codex models, all profiles, all sessions → blocked for 38 minutes from one 429.
- State persists in `~/.openclaw/agents/main/agent/auth-state.json` and **survives gateway restart**. `launchctl kickstart … ai.openclaw.gateway` does NOT clear it.
- File schema:
  ```json
  {
    "version": 1,
    "lastGood": {"openai-codex": "openai-codex:nicolasaubert78@hotmail.com"},
    "usageStats": {
      "openai-codex:nicolasaubert78@hotmail.com": {
        "errorCount": 1,
        "lastUsed": 1777149276347,
        "cooldownUntil": 1777154242907,
        "cooldownReason": "rate_limit",
        "cooldownModel": "gpt-5.5",
        "failureCounts": {"rate_limit": 1},
        "lastFailureAt": 1777149296407
      }
    }
  }
  ```

**Manual cooldown clear** (when the user is locked out and you can't wait):

```sh
cat > ~/.openclaw/agents/main/agent/auth-state.json <<'JSON'
{"version":1,"lastGood":{"openai-codex":"openai-codex:nicolasaubert78@hotmail.com"},"usageStats":{}}
JSON
chmod 600 ~/.openclaw/agents/main/agent/auth-state.json
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

Replace the `lastGood` value with whatever was previously there for the relevant provider.

> **Pro subscription does NOT auto-bypass the cooldown.** Even after upgrading Nicolas from Plus to Pro ($100/mo), the cooldown persisted because it's an openclaw-local state, not an upstream account state. Clear manually after upgrade if there's an active cooldown.

**TODO (future):** find the openclaw config keys for `cooldownDurationMs`, `failureThresholdBeforeCooldown`, etc. so we can shorten cooldowns and require multiple consecutive failures. The bundled `dist/` is minified/obfuscated so simple grep didn't find them. Likely in `agents.defaults.providers.<id>.cooldown` or top-level `rateLimits`.

## 7. Telegram channel + pairing flow

Confirmed working pattern:

```sh
openclaw channels add --channel telegram --token <BOT_TOKEN_FROM_BOTFATHER>
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway   # required for the bot to start polling
# Have the user DM the bot once. Bot replies with "Pairing pending. Code: XXXXXXXX"
openclaw pairing approve telegram XXXXXXXX
# → "Approved telegram sender <numeric user id>"
```

That last command takes 30–60 s on this Mac mini because the openclaw CLI has a 30 s+ Node startup + gateway-resolve cost. Don't assume it's hung.

## 8. Memory stack final architecture (recommended for Intel/16 GB)

```
┌─ Lossless Claw 0.9.2 (context engine slot)              ✅ deployed
│
├─ OpenClaw built-in memory                                ✅ deployed
│    ├─ provider: gemini, model: gemini-embedding-001
│    └─ ~/.openclaw/workspace/memory/*.md (sqlite-vec)
│
├─ mem0 v2.0.0 (structured memory)                         ✅ deployed
│    ├─ ~/.openclaw/data/mem0/venv/ (system Python 3.9 OK)
│    ├─ Gemini for both LLM + embeddings
│    └─ chroma store at ~/.openclaw/data/mem0/store/
│
└─ Graphiti 0.30.x + FalkorDB Docker (typed/temporal KG)   ✅ deployed
     ├─ uv venv with Python 3.12, graphiti-core[falkordb,google-genai]
     ├─ ~/.openclaw/data/structured/graphiti/venv/
     ├─ Docker container `flyn-falkordb` (falkordb/falkordb:latest)
     │    --memory 1g --memory-reservation 512m
     │    -p 127.0.0.1:6379:6379 -p 127.0.0.1:3000:3000
     │    -v ~/.openclaw/data/falkordb:/data
     └─ Multi-tenant via group_id
```

Total steady-state RAM: ~1.5 GB. Out of 16 GB host = comfortable.

**Skip permanently on Intel:**
- Ollama + `gemma4:e4b` heartbeat — no Metal, ~1–3 tok/s, heartbeats time out
- Local embedding fallback (EmbeddingGemma) — same reason
- Neo4j default Docker config — ~3–5 GB incl. VM is too heavy

## 9. Graphiti backend on Intel — chosen FalkorDB over Kuzu

| Backend | Status | Why |
|---|---|---|
| Neo4j 5.26 default (1 GB heap) | ❌ skip | Docker VM + heap = 3-5 GB |
| Neo4j 5.26 shrunken (512 MB heap) | ⚠ workable | Tighter than FalkorDB, no advantage |
| **FalkorDB 1.1.2** | ✅ **chosen** | Redis-based, ~600 MB total, sub-10 ms queries, native graphiti driver, multi-tenant clean |
| Kuzu 0.11.3 (embedded, no Docker) | ❌ broken upstream | graphiti issue [#1132 "Kuzu is archived"](https://github.com/getzep/graphiti/issues/1132). Build + add work, **search fails** with `Binder exception: Table Entity doesn't have an index with name node_name_and_summary`. Reproducer at `deploy/memory-intel/graphiti-kuzu-smoke.py`. |

Smoke result on FalkorDB (verified 2026-04-25):
```
build OK
add OK
search results: 3
 - Nicolas Aubert uses an Intel Mac mini Late 2014.
 - Rungis is near Paris.
 - Nicolas Aubert is a freelance commercial agent at Rungis.
```

Three typed facts auto-extracted from one natural-language sentence and retrieved by semantic search.

## 10. Docker Desktop right-sizing for Intel/16 GB

Default Docker Desktop install reserves **4 CPU + 8 GB RAM** for the VM — half this machine, even with FalkorDB-only workload using 73 MiB.

Settings file: `~/Library/Group Containers/group.com.docker/settings.json` (key names confirmed on Docker 27.1.1, settingsVersion 38).

```python
# Patched these keys:
d["cpus"] = 2          # was 4
d["memoryMiB"] = 2048  # was 8092
d["swapMiB"] = 512     # was 1024
```

Then quit Docker.app + relaunch. **Important:** the daemon may show `backend.sock` while the VM is still booting — `docker info` will hang for 30–60 s the first time. If it hangs longer, check the Docker Desktop UI for a settings-confirmation dialog.

Verified post-resize:
- VM: `CPUs=2, Mem=2.06 GB`
- FalkorDB: `cpu=1.05%, mem=177.3 MiB / 1 GiB` (17% of cap)
- Headroom: ~1 GB free in VM, plenty for one more small container

**Bump VM only when:** adding a second container, or `docker stats` shows `memPerc > 70%` consistently.

## 11. mem0 v2 API gotchas

- `Memory.search(...)` v2 dropped top-level `user_id`. Use `filters={"user_id": ...}`.
- Default `gemini` embedder uses `google-genai` (new SDK), not `google-generativeai`. Install `google-genai` separately into the venv.
- Embedding model name is `gemini-embedding-001` (the `models/text-embedding-004` ID from older SDK is **404 NOT_FOUND** in v1beta).
- mem0 splits compound facts into atoms automatically (`add("Nicolas lives in France and uses Mac mini")` → 2 stored memories). ADD returns empty `results: []` on dedup hit; doesn't mean failure.

## 12. OpenClaw remote agent quirks (OAC operator side)

- When openclaw CLI tries to talk to a non-existent gateway, it can hang for 30–60 s before timing out. If multiple commands queue, the OAC remote agent's child queue serializes — every queued command waits.
- Long-hung child (e.g., `docker info` while daemon not booted) blocks the entire device-exec queue. Symptoms: server-side `running: [{commandSeq: N}]` for 5+ minutes, no new results draining.
- Recovery (must be done locally, since the agent itself is blocking remote exec):
  ```sh
  pkill -9 -f "docker info"; pkill -9 -f "docker ps"
  launchctl kickstart -k gui/$(id -u)/com.openclaw.remote-agent
  ```
- Memory rule learned earlier: **keep device exec command strings under ~500 characters** to avoid zsh bracketed-paste hangs. Long heredocs, splitting long commands across multiple exec calls works better.

## 13. CRM workspace pattern (Nicolas-specific but generalizable)

The agent self-built a `~/.openclaw/workspace/business_crm/` with:
- `data/nicolas_crm.sqlite` — accounts table
- `scripts/discover_places.py` — Google Places API search → SQLite
- `scripts/enrich_websites.py` — pulls homepages, parses contacts
- `scripts/sync_sheets.py` — bidirectional Google Sheets sync via `gog`
- `playbooks/*.md` — agent-readable runbooks

This is a working pattern for an agent that needs structured business data. Worth promoting into a deploy skill once we see it work for 1–2 more clients.

## 14. Final Nicolas state (2026-04-25)

| Component | State |
|---|---|
| OAC remote agent | ✅ device `c019abc2…`, launchd, online |
| Xcode CLT | ✅ installed (admin) |
| Homebrew | ✅ at `/usr/local/bin/brew` (admin) |
| OpenClaw | ✅ 2026.4.23, gateway PID rotates, lossless-claw bound |
| Node + npm | ✅ 22.22.0 / 10.9.4 in `~/.openclaw/bin/` (x86_64) |
| Auth profiles | ✅ `gemini:default`, `google:default`, `google-places:default`, `openai-codex:nicolasaubert78@hotmail.com` (ChatGPT Pro $100/mo) |
| Heartbeat | ✅ `gemini-2.5-flash` |
| Primary chat model | ✅ `openai-codex/gpt-5.5` (Pro subscription) |
| Telegram bot | ✅ `@alex_9000_bot` polling, sender `6929245940` paired |
| Memory: Lossless Claw | ✅ active, processing turns live |
| Memory: OpenClaw built-in | ✅ Gemini embeddings, indexed |
| Memory: mem0 | ✅ at `~/.openclaw/data/mem0/`, Gemini stack |
| Memory: Graphiti+FalkorDB | ✅ docker `flyn-falkordb`, smoke 3/3 |
| Docker resources | ✅ 2 CPU / 2 GB / 512 MB swap (down from 4 / 8 / 1) |
| CRM workspace | ✅ self-built by agent during chat |

## 15. Open issues / future work

- [ ] Find openclaw config keys for cooldown duration + failure threshold; current 38-min single-failure cooldown is too aggressive.
- [ ] Decide canonical fallback chain — `openai-codex/gpt-5.5` primary, but what fallbacks? `gemini-2.5-flash` works as bare ID; the codex `gpt-5-codex` ID was rejected. Lookup before committing.
- [ ] Optional: REST wrapper around Graphiti (mirror the Apple-Silicon `flyn-graphiti-api.py`) so the agent can `curl` the KG from inside its tool surface.
- [ ] Optional: install Obsidian + `obsidian-mcp-server` if Nicolas wants a human-facing markdown vault.
- [ ] Re-run `graphiti-kuzu-smoke.py` periodically; if upstream fixes the Kuzu regression, switch from FalkorDB back to embedded Kuzu (no Docker overhead).
- [ ] Capture the exact rate-limit-rule schema once located.
