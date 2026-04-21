---
name: openclaw-macos-nonadmin-install
description: On macOS clients without admin rights, skip Homebrew — install Node via direct tarball to ~/.local/node, then npm install -g openclaw
type: feedback
originSessionId: 491d320c-9f90-4138-aeca-ab203843dc02
---
When installing OpenClaw on a macOS client where the user is NOT a local administrator, skip Homebrew entirely and use a direct Node tarball + user-prefix npm install. The official `curl https://openclaw.ai/install.sh | bash` fails on non-admin accounts because it tries to install Homebrew, which demands sudo.

**Why:** Brian Greenleaf's Mac Mini (2026-04-09 install) had user `avery` who wasn't an admin. The OpenClaw installer detected macOS → chose npm install method → tried to install Homebrew as a prerequisite → `sudo` failed with "Need sudo access on macOS (e.g. the user avery needs to be an Administrator)". Also `fnm`'s installer prefers Homebrew by default (`--skip-shell` doesn't help). The direct Node tarball path avoids both traps and is completely user-level.

**How to apply:** For non-admin macOS clients, run these steps (commands go through `/tmp/oac-run.sh` or equivalent):

```bash
# 1. Download + extract Node v22 LTS (no brew, no sudo)
NODEVER=v22.14.0
mkdir -p ~/.local && cd ~/.local && \
  curl -fsSL https://nodejs.org/dist/$NODEVER/node-$NODEVER-darwin-arm64.tar.gz -o node.tar.gz && \
  tar -xzf node.tar.gz && rm -f node.tar.gz && \
  rm -rf node && mv node-$NODEVER-darwin-arm64 node

# 2. Add node to PATH for the install
export PATH=~/.local/node/bin:$PATH && node --version && npm --version

# 3. Install openclaw — uses the local node's prefix, binary lands in ~/.local/node/bin
npm install -g openclaw@latest

# 4. Persist PATH for future login shells
grep -q "/.local/node/bin" ~/.zprofile || \
  echo 'export PATH=$HOME/.local/node/bin:$PATH' >> ~/.zprofile

# 5. Onboard (needs --accept-risk for non-interactive mode)
openclaw onboard --install-daemon --non-interactive --accept-risk
```

**Detection before install:** Run `sw_vers -productVersion; dseditgroup -o checkmember -m $(whoami) admin 2>&1 | tail -1`. If that reports "is NOT a member of admin", use this path instead of the official installer.

**For Apple Silicon (arm64)** use `darwin-arm64` tarball; **for Intel** use `darwin-x64`. Detect with `uname -m`.

**Note:** The bundled `~/.openclaw-remote/node` (from the OAC agent bundle) is a standalone node runtime without `npm` in its `node_modules` — don't try to reuse it for installing openclaw.
