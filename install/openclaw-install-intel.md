# Install OpenClaw — Intel Mac (x86_64)

Variant of `install/openclaw-install.md` for **Intel-based Macs** (pre-Apple-Silicon hardware: any Mac mini ≤ 2018, MacBook Pro / iMac ≤ 2020, Mac Pro ≤ 2019).

> **Branch:** `intel-mac-support` in `flyn-agent`. Findings captured live during Nicolas Aubert's onboarding (2026-04-25, Mac mini Late 2014 `Macmini7,1`, macOS 12.7.6, 16 GB).

## How this differs from the standard install

| Concern | Apple Silicon (default) | Intel x86_64 (this doc) |
|---|---|---|
| Heartbeat model | `ollama/gemma4:e4b` (Metal-accelerated, ~9.6 GB) | **Cloud-only** — `gemini-2.5-flash` or `claude-haiku-4-5`. Ollama on Intel without Metal/MPS is too slow for a heartbeat loop. |
| Embeddings | Cloud `gemini-embedding-2-preview` + local `EmbeddingGemma` fallback | Cloud-only. Skip the local fallback to save RAM. |
| Homebrew prefix | `/opt/homebrew` | `/usr/local` — `brew --prefix` resolves it, never hard-code. |
| Node bundled binary | `arm64` build | **must be `x86_64`** — installer auto-detects, but verify with `file ~/.openclaw/bin/node`. |
| Quarantine attribute path | `~/.openclaw/bin/node` | Same, but on Intel Macs running macOS ≤12 you may need `xattr -dr com.apple.quarantine ~/.openclaw/` (recursive) instead of single-file. |
| Docker (for optional Neo4j) | Native virtualization, fast | HyperKit/Rosetta — slow + memory-hungry. Drop unless explicitly needed. |
| Graphiti / Neo4j stack | Recommended for full Flyn deploy | **Optional** on Intel — heartbeat + memory work fine without it. |

## Pre-flight checks (Intel-specific additions)

```sh
# Confirm x86_64 (NOT arm64)
uname -m
# Expected: x86_64

# Older macOS detection (12.x is the last that runs on Macmini7,1)
sw_vers -productVersion

# Intel Macs ship Bash 3.2 by default — but zsh is the user shell on macOS 10.15+.
echo "$SHELL"; bash --version | head -1
```

## Install (Intel)

> **⚠ Don't use `install.sh` over a remote / non-interactive session.** It picks the **npm** install path on macOS, which tries to install Homebrew if missing — and Homebrew needs `sudo` admin access. In a remote PTY (OAC session, SSH no-tty) the sudo prompt fails immediately with `Need sudo access on macOS`. Verified failure on Nicolas's Mac mini, 2026-04-25.
>
> Use the **local-prefix variant** instead. It bundles Node + OpenClaw under `~/.openclaw/`, never touches `/usr/local`, and never asks for sudo:

```sh
curl -fsSL https://openclaw.ai/install-cli.sh | bash
```

Then add the local bin to PATH for the current shell:

```sh
export PATH="$HOME/.openclaw/bin:$PATH"
echo 'export PATH="$HOME/.openclaw/bin:$PATH"' >> ~/.zshrc
```

Verify the bundled Node is Intel:

```sh
file ~/.openclaw/bin/node
# Expected: Mach-O 64-bit executable x86_64
```

If the binary reports `arm64`, abort — installer detection failed. Force the arch:

```sh
ARCH=x86_64 curl -fsSL https://openclaw.ai/install-cli.sh | bash
```

### When to use the standard `install.sh` instead

Only when **all three** are true:
1. You're sitting at the physical keyboard (real TTY, can answer sudo prompts)
2. `brew` is already installed (so the installer won't prompt for admin)
3. You want OpenClaw on the global npm prefix (rare for client deploys)

Otherwise default to `install-cli.sh` on Intel Macs — same behavior we use for Apple Silicon clients without admin rights.

## Onboarding (Intel)

> **⚠ `--non-interactive` alone is not enough on 2026.4.23.** Onboarding shows a security confirmation prompt ("I understand this is personal-by-default…") that `--skip-ui` and `--non-interactive` do **not** bypass on their own. The CLI rejects `--non-interactive` unless `--accept-risk` is also passed.
>
> Verified on Nicolas Aubert's Mac mini, 2026-04-25 — onboard hung at the prompt for 2+ minutes with no output until the user-side selector was activated.

Use the full non-interactive flag set:

```sh
openclaw onboard \
  --non-interactive --accept-risk \
  --mode local \
  --skip-channels --skip-search --skip-skills --skip-ui --skip-health
```

> **Why each flag matters on Intel:**
> - `--non-interactive --accept-risk` — pair required to suppress the security gate.
> - `--mode local` — onboard for a single host (the alternative `remote` mode wires up a different gateway topology).
> - `--skip-channels` — Telegram/Discord/Slack come later via `deploy-messaging-setup.md`.
> - `--skip-search` — search providers come later.
> - `--skip-skills` — skill installs come later.
> - `--skip-ui` — Control UI/TUI selection.
> - `--skip-health` — Intel Macs without warmed dependencies fail the post-install probe; we run `openclaw doctor` after instead.

After onboarding, **swap the heartbeat model away from Ollama** before any heartbeat fires (default templates assume Apple Silicon):

```sh
openclaw config set agents.defaults.heartbeat.model "gemini/gemini-2.5-flash"
openclaw config set agents.defaults.heartbeat.isolatedSession true --strict-json
```

Or, if Anthropic credentials are available:

```sh
openclaw config set agents.defaults.heartbeat.model "anthropic/claude-haiku-4-5"
```

Validate:

```sh
openclaw config validate
```

## Skip on Intel (vs. Apple Silicon Flyn deploy)

These steps from `deploy/install-flyn.sh` are **Apple-Silicon-only** — skip them on Intel:

1. **Step 1 — Ollama + `gemma4:e4b`.** On Intel without Metal the model runs at ~1–3 tok/s. The heartbeat loop will time out. Use a cloud heartbeat instead.
2. **Step 3 — Neo4j Docker.** Optional. If you enable it, give Docker Desktop ≤ 4 GB RAM (machine has 16 GB total) and use `--memory-reservation 1g`.
3. **Step 4 — Graphiti venv.** Skip unless Step 3 was kept.
4. **Step 5 — `flyn-graphiti-api` launchd unit.** Skip unless Steps 3 + 4 were kept.

## Findings log (Nicolas Aubert, 2026-04-25)

<!-- Filled in as the install runs. -->

- **Hardware:** Mac mini 7,1 (Late 2014), Intel Core i5, 4 cores, 16 GB.
- **OS:** macOS 12.7.6 (Monterey, build 21H1320). 12.x is the latest macOS Macmini7,1 supports.
- **System Node:** none (`NO_NODE`). OpenClaw installer must provision its own.
- **Locale:** French (CEST) — date strings in logs are localized.
- **Hostname:** `Mac-mini-de-Didi`, primary user `Didi` (uid 502).
- **Persistent remote agent:** OpenAgent Connect device `c019abc2…` enrolled, launchd `com.openclaw.remote-agent` running, x86_64 node bundle confirmed working.
- **OpenClaw install (attempt 1, `install.sh`):** ❌ FAILED. Installer chose `npm` method, tried to install Homebrew, hit `Need sudo access on macOS (e.g. the user Didi needs to be an Administrator)!`. Non-interactive remote PTY can't supply sudo password. Lesson: **never run `install.sh` on a remote-only client** — promoted to top of this doc.
- **OpenClaw install (attempt 2, `install-cli.sh`):** ✅ Installed `OpenClaw 2026.4.23 (a979721)` in 32s. 469 npm packages, Node 22.22.0 user-space. Bundled `node` is `Mach-O 64-bit executable x86_64` — Intel-compatible. No sudo, no brew.
- **Onboarding (attempt 1, `--install-daemon --non-interactive`):** ❌ Silent failure (exit 0, zero output, no files created). Cause: undocumented requirement of `--accept-risk` for `--non-interactive` on 2026.4.23 — the security confirmation prompt blocks even with `--skip-ui`.
- **Onboarding (attempt 2, with `--accept-risk`):** ✅ openclaw.json + workspace + sessions created in ~30s.
- **Onboarding (attempt 3, adding `--install-daemon`):** ✅ Installed `~/Library/LaunchAgents/ai.openclaw.gateway.plist`, gateway PID 2621 listening on `ws://127.0.0.1:18789`.
- **`openclaw doctor --non-interactive --yes`:** ⚠ Partial. Bundled deps mostly installed (`@anthropic-ai/sdk`, `@google/genai`, `playwright-core`, etc.). Missing: `node-edge-tts` (Microsoft TTS) and `libsignal-node` (WhatsApp/Signal) — both fail at `git ls-remote` because **Xcode Command Line Tools are not installed**. Same root cause as the Homebrew git-clone failure.
- **Homebrew (attempt 1, git clone):** ❌ Blocked — `xcode-select` GUI dialog can't be answered remotely.
- **Homebrew (attempt 2, tarball fallback):** ✅ `~/homebrew/bin/brew --version → Homebrew >=4.3.0 (shallow or no git repository)`. Binary works for `brew --version`, but **shallow brew can't tap, update, or install** without git. Useful only as a placeholder until CLT is in place.
- **Xcode Command Line Tools install:** Triggered manually by an admin on Nicolas's side at 2026-04-25 ~17:42 UTC, ETA ~16 min. Once it finishes, redo: (1) `git clone --depth=1 https://github.com/Homebrew/brew ~/homebrew`, (2) `openclaw doctor --fix --non-interactive --yes` to pull `node-edge-tts` + `libsignal-node`.
- **Heartbeat decision:** ✅ Set to `gemini/gemini-2.5-flash` after Gemini key was provisioned (2026-04-25). Wrote `auth-profiles.json` with `google:default` + `gemini:default` aliases plus `google-places:default`, chmod 600. Direct curl test of the key returned HTTP 200. `openclaw config validate` → `Config valid`. Confirmed: do **not** use `ollama/gemma4:e4b` on Intel — Mac mini 7,1 has no Metal accel.

## Admin / non-admin user paths

Nicolas's `Didi` account is **not a macOS administrator**, which propagated through the entire install. Document this distinction explicitly for future Intel clients:

| Need | Admin user | Non-admin user (Nicolas) |
|---|---|---|
| OpenClaw install | `install.sh` (npm prefix) or `install-cli.sh` | **`install-cli.sh` only** |
| Homebrew | Standard `install.sh` to `/usr/local` | Git-clone to `~/homebrew` *after* CLT is provisioned by an admin |
| Xcode Command Line Tools | `xcode-select --install` (GUI confirms with admin password) | An admin must run `xcode-select --install` for the user, OR `softwareupdate -i 'Command Line Tools for Xcode-X.Y' --no-scan` as admin |
| OpenClaw plugin runtime deps that pull from git (`node-edge-tts`, `libsignal-node`) | Available after CLT install | Available after admin-installed CLT |
| Loading user-level launchd services (`com.openclaw.remote-agent`, `ai.openclaw.gateway`) | Works | Works (user LaunchAgents only) |

## Verification command set (post-install)

After Phase 2 + Xcode CLT, run these on the client to confirm full health:

```sh
~/.openclaw/bin/openclaw --version
~/.openclaw/bin/openclaw gateway status | head
~/.openclaw/bin/openclaw doctor --non-interactive --yes 2>&1 | tail -30
file ~/.openclaw/tools/node/bin/node          # → x86_64
launchctl list | grep -E "openclaw|com.openclaw"
~/homebrew/bin/brew --version                  # only if CLT done
```

## Troubleshooting (Intel additions)

| Problem | Cause | Fix |
|---|---|---|
| `file ~/.openclaw/bin/node` reports `arm64` | Installer detected arch wrong (rare) | `ARCH=x86_64 curl -fsSL https://openclaw.ai/install.sh \| bash` |
| `gemma4:e4b` heartbeat hangs | No Metal accel on Intel — model too slow | Switch heartbeat to cloud: `openclaw config set agents.defaults.heartbeat.model "gemini/gemini-2.5-flash"` |
| `brew: command not found` | brew not installed | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` — installs to `/usr/local` on Intel |
| Docker Desktop sluggish | HyperKit virtualization on Intel | Reduce Docker Desktop resources to ≤4 GB RAM, ≤2 CPUs, OR skip Neo4j/Graphiti entirely |
| `xattr -d` fails per-file | Whole bundle quarantined | `xattr -dr com.apple.quarantine ~/.openclaw/` |
