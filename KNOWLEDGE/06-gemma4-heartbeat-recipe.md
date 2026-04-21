---
name: OpenClaw + Ollama + Gemma 4 local heartbeat working recipe
description: Proven config for routing OpenClaw heartbeat/background to Ollama Gemma 4 locally, with tool calls working, on Apple Silicon.
type: feedback
originSessionId: b6add74d-697e-4ae2-a0e0-e9dfb6dbcc2f
---
Validated working recipe for running OpenClaw heartbeat on local Gemma 4 via Ollama — confirmed on Mac Mini 4C, OpenClaw 2026.4.15, Ollama 0.21.0, 16GB RAM, 2026-04-21.

**The tag is `gemma4:e4b`** (9.6 GB download, ~11 GB in Metal memory at runtime). Gemma 4 natively advertises `["completion","vision","audio","tools","thinking"]` in its `/api/show` capabilities — unlike gemma3:4b which lacks `tools` and gets rejected by OpenClaw's request assembly with HTTP 400 "provider rejected the request schema or tool payload."

**Config changes** (via `openclaw config set`):
```
agents.defaults.heartbeat.model = "ollama/gemma4:e4b"
agents.defaults.heartbeat.isolatedSession = true
agents.defaults.heartbeat.suppressToolErrorWarnings = true
agents.defaults.models."ollama/gemma4:e4b" = {}
```

**Auth profile** (must exist even though Ollama is local — OpenClaw errors 401 otherwise). Add to `~/.openclaw/agents/<id>/agent/auth-profiles.json`:
```json
"ollama:default": { "type": "token", "provider": "ollama", "token": "local" }
```

**Install commands:**
```bash
brew install ollama && brew services start ollama
tmux new-session -d -s g4-pull "ollama pull gemma4:e4b"   # 9.6 GB
# then the config set lines above
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway   # restart gateway (or config hot-reload)
```

**Verification path (NOT via `openclaw capability model run`):**
- `ollama ps` — should show `gemma4:e4b ... 100% GPU` when running
- Ollama log (`/opt/homebrew/var/log/ollama.log`) — look for `gemma4: token IDs`, `model weights device=Metal`, aligned with the time of a heartbeat event
- `/tmp/openclaw/openclaw-YYYY-MM-DD.log` — absence of new `model_fallback_decision` entries = success. Presence with `requestedProvider: "ollama"` + `reason: "format"` = still broken.

**Why `openclaw capability model run --model X` lies:** The `--model` flag on `capability model run` does not bind — responses still show the agent's default primary (`openai-codex/gpt-5.4-mini`) even when routing is actually happening correctly on other code paths. **Do not use this CLI as a routing probe.** Ollama logs + fallback decision log are the real source of truth.

**Gotchas:**
- Gemma 3 (any size) is NOT tool-capable via Ollama — always fails with 400. Use Gemma 4 or qwen2.5:7b.
- `gemma4:4b` is not a valid tag — it's `gemma4:e4b` (edge-4B variant).
- `gemma4:e4b` takes ~11 GB on Metal at runtime; tight on 16 GB hosts. `gemma4:e2b` (7.2 GB disk) is the smaller fallback if RAM pressure becomes real.
- Keep `isolatedSession: true` on heartbeat so heartbeat turns don't pollute main-session context.
- `suppressToolErrorWarnings: true` on heartbeat silences noise; doesn't mask real model-fallback events (those go to a different log subsystem).
